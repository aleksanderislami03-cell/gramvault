"""Tests for `gramvault.chat.service`: persistence, citation parsing, the
streaming RAG flow, and semantic search.

Ollama (`gramvault.ai.ollama_client`) and the retrieval layer are mocked
throughout — no real Ollama server, model, or ChromaDB instance required.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from gramvault.ai.ollama_client import ModelNotPulledError, OllamaNotRunningError
from gramvault.chat import service
from gramvault.chat.retrieval import RetrievalResult
from gramvault.config import Config
from gramvault.models.schemas import ChatRole, Item, MediaType


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _fake_stream_chat(messages, model=None, config=None):
    for chunk in ["Sure — ", "here's what I found [[item:1]] and also [[item:999]]."]:
        yield chunk


async def _raising_stream_chat(messages, model=None, config=None):
    raise OllamaNotRunningError("http://localhost:11434")
    yield  # pragma: no cover - makes this an async generator function


class TestSessionPersistence:
    def test_create_and_get_session(self, tmp_db_conn: sqlite3.Connection) -> None:
        session = service.create_session(tmp_db_conn, title="My chat")
        assert session.id is not None
        assert session.title == "My chat"

        fetched = service.get_session(tmp_db_conn, session.id)
        assert fetched is not None
        assert fetched.id == session.id

    def test_get_missing_session_returns_none(self, tmp_db_conn: sqlite3.Connection) -> None:
        assert service.get_session(tmp_db_conn, 9999) is None

    def test_list_sessions_most_recent_first(self, tmp_db_conn: sqlite3.Connection) -> None:
        first = service.create_session(tmp_db_conn, title="first")
        second = service.create_session(tmp_db_conn, title="second")

        sessions = service.list_sessions(tmp_db_conn)

        assert [s.id for s in sessions][:2] == [second.id, first.id]

    def test_list_messages_includes_citations(self, tmp_db_conn: sqlite3.Connection) -> None:
        session = service.create_session(tmp_db_conn)
        message_id = service._persist_message(tmp_db_conn, session.id, ChatRole.ASSISTANT, "hi [[item:1]]")
        tmp_db_conn.execute(
            "INSERT INTO items (media_type, caption) VALUES ('photo', 'x')"
        )
        tmp_db_conn.commit()
        tmp_db_conn.execute(
            "INSERT INTO chat_citations (message_id, item_id, snippet) VALUES (?, 1, 'snip')",
            (message_id,),
        )
        tmp_db_conn.commit()

        messages = service.list_messages(tmp_db_conn, session.id)

        assert len(messages) == 1
        assert messages[0].citations[0].item_id == 1
        assert messages[0].citations[0].snippet == "snip"


class TestParseCitations:
    def test_extracts_valid_citations_in_order_deduped(self) -> None:
        text = "See [[item:2]] and [[item:1]] and again [[item:2]]."
        result = service.parse_citations(text, valid_item_ids={1, 2})
        assert result == [2, 1]

    def test_drops_hallucinated_ids_not_in_valid_set(self) -> None:
        text = "See [[item:1]] and [[item:42]]."
        result = service.parse_citations(text, valid_item_ids={1})
        assert result == [1]

    def test_no_markers_returns_empty(self) -> None:
        assert service.parse_citations("no citations here", valid_item_ids={1, 2}) == []


class TestEnsureOllamaReady:
    @pytest.mark.anyio
    async def test_checks_running_and_both_models(self, tmp_config: Config) -> None:
        with (
            patch.object(service.ollama_client, "ensure_running", new_callable=AsyncMock) as mock_running,
            patch.object(
                service.ollama_client, "ensure_model_pulled", new_callable=AsyncMock
            ) as mock_pulled,
        ):
            await service.ensure_ollama_ready(tmp_config)

        mock_running.assert_awaited_once()
        assert mock_pulled.await_count == 2
        checked_models = {call.args[0] for call in mock_pulled.await_args_list}
        assert checked_models == {tmp_config.models.chat_model, tmp_config.models.embedding_model}


class TestStreamMessage:
    @pytest.mark.anyio
    async def test_streams_tokens_persists_message_and_valid_citations_only(
        self, tmp_config: Config
    ) -> None:
        from gramvault.db.session import get_connection, init_db

        conn = get_connection(tmp_config)
        init_db(conn)
        session = service.create_session(conn, title="test")
        conn.execute(
            "INSERT INTO items (id, media_type, caption) VALUES (1, 'photo', 'a pasta recipe')"
        )
        conn.commit()
        item = Item(id=1, media_type=MediaType.PHOTO, caption="a pasta recipe")
        conn.close()

        fake_result = RetrievalResult(item_id=1, score=0.9, snippet="a pasta recipe")

        with (
            patch.object(
                service.retrieval, "hybrid_search", new_callable=AsyncMock
            ) as mock_hybrid,
            patch.object(service.retrieval, "fetch_items", return_value={1: item}),
            patch.object(service.ollama_client, "stream_chat", new=_fake_stream_chat),
        ):
            mock_hybrid.return_value = [fake_result]

            events = [
                event
                async for event in service.stream_message(session.id, "any pasta?", config=tmp_config)
            ]

        token_events = [e for e in events if e["event"] == "token"]
        done_events = [e for e in events if e["event"] == "done"]
        assert len(done_events) == 1
        assert not [e for e in events if e["event"] == "error"]

        full_content = "".join(json.loads(e["data"])["content"] for e in token_events)
        assert "Sure" in full_content

        done_payload = json.loads(done_events[0]["data"])
        # item 1 was actually retrieved -> kept; item 999 was not -> dropped
        cited_ids = {c["item_id"] for c in done_payload["citations"]}
        assert cited_ids == {1}

        # verify persistence: user + assistant messages, with citation row
        conn = get_connection(tmp_config)
        messages = service.list_messages(conn, session.id)
        conn.close()
        assert [m.role for m in messages] == [ChatRole.USER, ChatRole.ASSISTANT]
        assert messages[0].content == "any pasta?"
        assert messages[1].citations[0].item_id == 1

    @pytest.mark.anyio
    async def test_ollama_not_running_yields_error_event_not_exception(
        self, tmp_config: Config
    ) -> None:
        from gramvault.db.session import get_connection, init_db

        conn = get_connection(tmp_config)
        init_db(conn)
        session = service.create_session(conn, title="test")
        conn.close()

        with (
            patch.object(service.retrieval, "hybrid_search", new_callable=AsyncMock, return_value=[]),
            patch.object(service.retrieval, "fetch_items", return_value={}),
            patch.object(service.ollama_client, "stream_chat", new=_raising_stream_chat),
        ):
            events = [
                event
                async for event in service.stream_message(session.id, "hello", config=tmp_config)
            ]

        assert events[-1]["event"] == "error"
        assert "Ollama" in json.loads(events[-1]["data"])["detail"]


class TestSemanticSearch:
    @pytest.mark.anyio
    async def test_returns_item_score_snippet_dicts(self, tmp_config: Config) -> None:
        item = Item(id=5, media_type=MediaType.PHOTO, caption="beach sunset")
        fake_result = RetrievalResult(item_id=5, score=0.75, snippet="beach sunset")

        with (
            patch.object(
                service.retrieval, "hybrid_search", new_callable=AsyncMock, return_value=[fake_result]
            ),
            patch.object(service.retrieval, "fetch_items", return_value={5: item}),
        ):
            results = await service.semantic_search("beach", top_k=5, config=tmp_config)

        assert len(results) == 1
        assert results[0]["item"].id == 5
        assert results[0]["score"] == 0.75
        assert results[0]["snippet"] == "beach sunset"

    @pytest.mark.anyio
    async def test_propagates_ollama_errors_for_route_to_translate(self, tmp_config: Config) -> None:
        with (
            patch.object(
                service.retrieval,
                "hybrid_search",
                new_callable=AsyncMock,
                side_effect=ModelNotPulledError("nomic-embed-text"),
            ),
            pytest.raises(ModelNotPulledError),
        ):
            await service.semantic_search("beach", config=tmp_config)
