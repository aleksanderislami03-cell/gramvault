"""Chat (RAG) + semantic search API — STUB for Agent A4.

Owns: chat sessions/messages backed by SQLite, streaming assistant
responses over Server-Sent Events, and a semantic search endpoint over
the ChromaDB embeddings that Agent A3's enrichment pipeline writes.

Every handler below is fully signed but raises HTTP 501.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from gramvault.api.deps import get_config_dependency
from gramvault.config import Config
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


@router.post("/sessions", response_model=ChatSession, status_code=201)
async def create_chat_session(
    body: ChatSessionCreateRequest,
    config: Config = Depends(get_config_dependency),
) -> ChatSession:
    """Create a new chat session.

    TODO(A4): insert into `chat_sessions` and return it.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A4 (chat/search)")


@router.get("/sessions", response_model=list[ChatSession])
async def list_chat_sessions(
    config: Config = Depends(get_config_dependency),
) -> list[ChatSession]:
    """List chat sessions, most recently updated first.

    TODO(A4): `SELECT * FROM chat_sessions ORDER BY updated_at DESC`.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A4 (chat/search)")


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessage])
async def list_chat_messages(
    session_id: int,
    config: Config = Depends(get_config_dependency),
) -> list[ChatMessage]:
    """List messages (with citations) in a session, oldest first.

    TODO(A4): join `chat_messages` with `chat_citations`.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A4 (chat/search)")


@router.post("/sessions/{session_id}/messages")
async def send_chat_message(
    session_id: int,
    body: ChatMessageCreateRequest,
    config: Config = Depends(get_config_dependency),
):
    """Send a user message and stream back the assistant's RAG response.

    TODO(A4): implement as a Server-Sent Events stream using
    `sse_starlette.sse.EventSourceResponse` (already a project dependency).
    Expected behavior:
      1. Persist the user message to `chat_messages`.
      2. Retrieve relevant chunks from ChromaDB (embed `body.content` with
         the configured `models.embedding_model` via Ollama) to ground
         the response.
      3. Stream the chat model's (`models.chat_model`) response token-by-
         token as SSE `data:` events.
      4. Persist the assistant message + `chat_citations` rows (pointing
         at the `items`/`media_files` that grounded the answer) once the
         stream completes.
    No response_model is declared here since the real implementation
    returns a streaming response rather than a JSON body.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A4 (chat/search)")


@router.get("/search", response_model=SemanticSearchResponse)
async def semantic_search(
    q: str = Query(..., description="Free-text query to search over the library semantically"),
    top_k: int = Query(default=10, ge=1, le=100),
    config: Config = Depends(get_config_dependency),
) -> SemanticSearchResponse:
    """Semantic search over the library (captions/transcripts/vision
    captions), independent of the chat flow — used for a "search bar"
    style experience in the frontend.

    TODO(A4): embed `q` with the configured embedding model, query
    ChromaDB for the top `top_k` nearest chunks, resolve them back to
    `Item`s, and return with similarity scores + a matching snippet.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A4 (chat/search)")
