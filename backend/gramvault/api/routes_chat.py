"""Chat (RAG) + semantic search API — implemented by Agent A4.

Owns: chat sessions/messages backed by SQLite, streaming assistant
responses over Server-Sent Events, and a semantic search endpoint over
the ChromaDB embeddings that Agent A3's enrichment pipeline writes.

RAG orchestration (retrieval, prompt construction, streaming completion,
citation persistence) lives in `gramvault.chat.service` — this module is
just the HTTP surface + friendly error translation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from gramvault.ai.ollama_client import ModelNotPulledError, OllamaNotRunningError
from gramvault.api.deps import get_config_dependency
from gramvault.chat import service
from gramvault.config import Config
from gramvault.db.session import session_scope
from gramvault.models.schemas import ChatMessage, ChatSession, Item

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatSessionCreateRequest(BaseModel):
    title: str | None = None


class ChatMessageCreateRequest(BaseModel):
    content: str


class SemanticSearchResult(BaseModel):
    item: Item
    score: float
    snippet: str | None = None


class SemanticSearchResponse(BaseModel):
    query: str
    results: list[SemanticSearchResult]


def _as_http_error(exc: OllamaNotRunningError | ModelNotPulledError) -> HTTPException:
    """Translate an Ollama readiness failure into a clean 503 with an
    actionable message, instead of letting it bubble up as a raw 500."""
    return HTTPException(status_code=503, detail=str(exc))


@router.post("/sessions", response_model=ChatSession, status_code=201)
async def create_chat_session(
    body: ChatSessionCreateRequest,
    config: Config = Depends(get_config_dependency),
) -> ChatSession:
    """Create a new chat session."""
    with session_scope(config) as conn:
        return service.create_session(conn, title=body.title)


@router.get("/sessions", response_model=list[ChatSession])
async def list_chat_sessions(
    config: Config = Depends(get_config_dependency),
) -> list[ChatSession]:
    """List chat sessions, most recently updated first."""
    with session_scope(config) as conn:
        return service.list_sessions(conn)


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessage])
async def list_chat_messages(
    session_id: int,
    config: Config = Depends(get_config_dependency),
) -> list[ChatMessage]:
    """List messages (with citations) in a session, oldest first."""
    with session_scope(config) as conn:
        if service.get_session(conn, session_id) is None:
            raise HTTPException(status_code=404, detail=f"Chat session {session_id} not found")
        return service.list_messages(conn, session_id)


@router.post("/sessions/{session_id}/messages")
async def send_chat_message(
    session_id: int,
    body: ChatMessageCreateRequest,
    config: Config = Depends(get_config_dependency),
):
    """Send a user message and stream back the assistant's RAG response as
    Server-Sent Events.

    Event stream shape (see `gramvault.chat.service.stream_message`):
        event: token   data: {"content": "..."}          (0+ times)
        event: done    data: {"message_id", "content", "citations": [...]}
        event: error   data: {"detail": "..."}           (terminal, instead
                                                            of "done", for a
                                                            mid-stream Ollama
                                                            failure)

    Citations are inline `[[item:<item_id>]]` markers in the streamed
    `content` — see `gramvault.chat.prompt` for the exact format contract
    (frontend/A5 parses these into clickable chips).

    404 if the session doesn't exist; 503 (before any streaming begins) if
    Ollama isn't running or the required models aren't pulled.
    """
    with session_scope(config) as conn:
        if service.get_session(conn, session_id) is None:
            raise HTTPException(status_code=404, detail=f"Chat session {session_id} not found")

    # Checked eagerly (before opening the event stream) so a friendly 503
    # is returned as a normal HTTP response rather than an SSE error event
    # after the client has already committed to a streaming connection.
    try:
        await service.ensure_ollama_ready(config)
    except (OllamaNotRunningError, ModelNotPulledError) as exc:
        raise _as_http_error(exc) from exc

    return EventSourceResponse(service.stream_message(session_id, body.content, config=config))


@router.get("/search", response_model=SemanticSearchResponse)
async def semantic_search(
    q: str = Query(..., description="Free-text query to search over the library semantically"),
    top_k: int = Query(default=10, ge=1, le=100),
    config: Config = Depends(get_config_dependency),
) -> SemanticSearchResponse:
    """Semantic search over the library (captions/transcripts/vision
    captions), independent of the chat flow — used for a "search bar"
    style experience in the frontend. No LLM call — embedding + vector
    search (merged with a keyword pass), same as chat retrieval but
    without the chat completion step.
    """
    try:
        raw_results = await service.semantic_search(q, top_k=top_k, config=config)
    except (OllamaNotRunningError, ModelNotPulledError) as exc:
        raise _as_http_error(exc) from exc

    return SemanticSearchResponse(
        query=q,
        results=[SemanticSearchResult(**r) for r in raw_results],
    )
