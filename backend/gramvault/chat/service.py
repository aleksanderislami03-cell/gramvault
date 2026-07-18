"""Orchestrates the RAG chat flow: persistence (chat_sessions/chat_messages/
chat_citations), retrieval, prompt construction, streaming chat completion,
and citation-marker parsing.

This module owns all the SQL for the chat feature area (routes_chat.py
should call into here rather than issuing its own SQL, mirroring the
retrieval/prompt split already in this package).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import AsyncIterator
from typing import Any

from gramvault.ai import ollama_client
from gramvault.chat import prompt, retrieval
from gramvault.config import Config, get_config
from gramvault.db.session import get_connection, init_db
from gramvault.models.schemas import ChatCitation, ChatMessage, ChatRole, ChatSession

DEFAULT_TOP_K = 6


# --- session/message persistence -------------------------------------------


def create_session(conn: sqlite3.Connection, title: str | None = None) -> ChatSession:
    cursor = conn.execute("INSERT INTO chat_sessions (title) VALUES (?)", (title,))
    conn.commit()
    session = get_session(conn, cursor.lastrowid)
    assert session is not None  # just inserted it
    return session


def get_session(conn: sqlite3.Connection, session_id: int) -> ChatSession | None:
    row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    return ChatSession(
        id=row["id"], title=row["title"], created_at=row["created_at"], updated_at=row["updated_at"]
    )


def list_sessions(conn: sqlite3.Connection) -> list[ChatSession]:
    rows = conn.execute(
        "SELECT * FROM chat_sessions ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    return [
        ChatSession(id=r["id"], title=r["title"], created_at=r["created_at"], updated_at=r["updated_at"])
        for r in rows
    ]


def list_messages(conn: sqlite3.Connection, session_id: int) -> list[ChatMessage]:
    """Messages in a session, oldest first, each with its citations."""
    message_rows = conn.execute(
        "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
    ).fetchall()
    messages: list[ChatMessage] = []
    for row in message_rows:
        citation_rows = conn.execute(
            "SELECT * FROM chat_citations WHERE message_id = ? ORDER BY id ASC", (row["id"],)
        ).fetchall()
        citations = [
            ChatCitation(
                id=c["id"],
                message_id=c["message_id"],
                item_id=c["item_id"],
                media_file_id=c["media_file_id"],
                snippet=c["snippet"],
            )
            for c in citation_rows
        ]
        messages.append(
            ChatMessage(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
                citations=citations,
            )
        )
    return messages


def _persist_message(conn: sqlite3.Connection, session_id: int, role: ChatRole, content: str) -> int:
    cursor = conn.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role.value, content),
    )
    conn.execute("UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,))
    conn.commit()
    return cursor.lastrowid


# --- citation parsing --------------------------------------------------------


def parse_citations(text: str, valid_item_ids: set[int]) -> list[int]:
    """Extract `[[item:<id>]]` markers from `text` (see prompt.py for the
    format contract), in first-seen order, deduped, filtered down to ids
    that were actually retrieved this turn — anything else is treated as
    a hallucinated citation and dropped."""
    seen: list[int] = []
    for match in re.finditer(prompt.CITATION_MARKER_REGEX, text):
        item_id = int(match.group(1))
        if item_id in valid_item_ids and item_id not in seen:
            seen.append(item_id)
    return seen


# --- Ollama readiness (friendly 503s) ---------------------------------------


async def ensure_ollama_ready(config: Config | None = None) -> None:
    """Raise `OllamaNotRunningError`/`ModelNotPulledError` if the chat or
    embedding model isn't ready. Call this BEFORE opening a streaming
    response, so the failure surfaces as a normal HTTP error rather than
    an SSE event after headers are already sent."""
    config = config or get_config()
    await ollama_client.ensure_running(config)
    await ollama_client.ensure_model_pulled(config.models.chat_model, config)
    await ollama_client.ensure_model_pulled(config.models.embedding_model, config)


# --- the main RAG chat flow --------------------------------------------------


async def stream_message(
    session_id: int,
    user_content: str,
    config: Config | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> AsyncIterator[dict[str, str]]:
    """Persist the user message, retrieve context, stream the assistant's
    RAG response, then persist the assistant message + citations.

    Yields SSE-event dicts (shape expected by
    `sse_starlette.sse.EventSourceResponse`):
        {"event": "token", "data": '{"content": "..."}'}       — 0+ times
        {"event": "done",  "data": '{"message_id", "content", "citations"}'}
        {"event": "error", "data": '{"detail": "..."}'}        — terminal, in
                                                                  place of "done"

    Opens and owns its own SQLite connection for the lifetime of the
    stream (a route handler can't hold a `with session_scope()` block open
    across the deferred consumption of a streaming response).
    """
    config = config or get_config()
    conn = get_connection(config)
    init_db(conn)
    try:
        history = [
            {"role": str(m.role), "content": m.content} for m in list_messages(conn, session_id)
        ]
        _persist_message(conn, session_id, ChatRole.USER, user_content)

        results = await retrieval.hybrid_search(conn, user_content, top_k=top_k, config=config)
        items = retrieval.fetch_items(conn, [r.item_id for r in results])
        valid_item_ids = set(items.keys())

        messages = prompt.build_messages(history, items, results, user_content)

        full_text = ""
        async for chunk in ollama_client.stream_chat(
            messages, model=config.models.chat_model, config=config
        ):
            full_text += chunk
            yield {"event": "token", "data": json.dumps({"content": chunk})}

        citation_item_ids = parse_citations(full_text, valid_item_ids)
        results_by_id = {r.item_id: r for r in results}

        assistant_message_id = _persist_message(conn, session_id, ChatRole.ASSISTANT, full_text)
        citations_payload: list[dict[str, Any]] = []
        for item_id in citation_item_ids:
            result = results_by_id.get(item_id)
            snippet = result.snippet if result else None
            media_file_id = result.media_file_id if result else None
            cite_cursor = conn.execute(
                "INSERT INTO chat_citations (message_id, item_id, media_file_id, snippet) "
                "VALUES (?, ?, ?, ?)",
                (assistant_message_id, item_id, media_file_id, snippet),
            )
            conn.commit()
            citations_payload.append(
                {
                    "id": cite_cursor.lastrowid,
                    "item_id": item_id,
                    "media_file_id": media_file_id,
                    "snippet": snippet,
                }
            )

        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "message_id": assistant_message_id,
                    "content": full_text,
                    "citations": citations_payload,
                }
            ),
        }
    except (ollama_client.OllamaNotRunningError, ollama_client.ModelNotPulledError) as exc:
        yield {"event": "error", "data": json.dumps({"detail": str(exc)})}
    finally:
        conn.close()


async def semantic_search(
    query: str, top_k: int = 10, config: Config | None = None
) -> list[dict[str, Any]]:
    """Plain (non-chat) semantic search for the gallery search box: embed +
    vector search, boosted/merged with a keyword pass, no LLM call.

    Returns a list of `{"item": Item, "score": float, "snippet": str | None}`
    dicts (the route wraps these into `SemanticSearchResult`).
    """
    config = config or get_config()
    conn = get_connection(config)
    init_db(conn)
    try:
        results = await retrieval.hybrid_search(conn, query, top_k=top_k, config=config)
        items = retrieval.fetch_items(conn, [r.item_id for r in results])
        return [
            {"item": items[r.item_id], "score": r.score, "snippet": r.snippet}
            for r in results
            if r.item_id in items
        ]
    finally:
        conn.close()
