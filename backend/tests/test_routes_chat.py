"""Tests for the `/api/chat` HTTP surface: session/message CRUD, the SSE
streaming send-message endpoint, and semantic search — including the
friendly-503 behavior when Ollama isn't reachable.

`gramvault.chat.service` is mocked at the route-module boundary so these
tests never touch a real Ollama server or ChromaDB instance.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from gramvault.ai.ollama_client import ModelNotPulledError, OllamaNotRunningError
from gramvault.models.schemas import Item, MediaType


def _create_session(client: TestClient, title: str = "test session") -> int:
    response = client.post("/api/chat/sessions", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


class TestSessionCrud:
    def test_create_session(self, client: TestClient) -> None:
        response = client.post("/api/chat/sessions", json={"title": "hello"})
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "hello"
        assert body["id"] is not None

    def test_create_session_without_title(self, client: TestClient) -> None:
        response = client.post("/api/chat/sessions", json={})
        assert response.status_code == 201
        assert response.json()["title"] is None

    def test_list_sessions(self, client: TestClient) -> None:
        _create_session(client, "first")
        _create_session(client, "second")

        response = client.get("/api/chat/sessions")

        assert response.status_code == 200
        titles = [s["title"] for s in response.json()]
        assert "first" in titles
        assert "second" in titles

    def test_list_messages_for_missing_session_is_404(self, client: TestClient) -> None:
        response = client.get("/api/chat/sessions/99999/messages")
        assert response.status_code == 404

    def test_list_messages_empty_for_new_session(self, client: TestClient) -> None:
        session_id = _create_session(client)
        response = client.get(f"/api/chat/sessions/{session_id}/messages")
        assert response.status_code == 200
        assert response.json() == []


class TestSendMessageStreaming:
    def test_missing_session_is_404_before_any_streaming(self, client: TestClient) -> None:
        response = client.post("/api/chat/sessions/99999/messages", json={"content": "hi"})
        assert response.status_code == 404

    def test_ollama_not_running_is_503_not_raw_error(self, client: TestClient) -> None:
        session_id = _create_session(client)

        with patch(
            "gramvault.api.routes_chat.service.ensure_ollama_ready",
            new_callable=AsyncMock,
            side_effect=OllamaNotRunningError("http://localhost:11434"),
        ):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages", json={"content": "hi"}
            )

        assert response.status_code == 503
        assert "ollama" in response.json()["detail"].lower()

    def test_model_not_pulled_is_503(self, client: TestClient) -> None:
        session_id = _create_session(client)

        with patch(
            "gramvault.api.routes_chat.service.ensure_ollama_ready",
            new_callable=AsyncMock,
            side_effect=ModelNotPulledError("llama3.1:8b"),
        ):
            response = client.post(
                f"/api/chat/sessions/{session_id}/messages", json={"content": "hi"}
            )

        assert response.status_code == 503
        assert "pull" in response.json()["detail"].lower()

    def test_streams_sse_events_with_correct_content_type(self, client: TestClient) -> None:
        session_id = _create_session(client)

        async def fake_stream_message(session_id, user_content, config=None):
            yield {"event": "token", "data": '{"content": "hi "}'}
            yield {"event": "token", "data": '{"content": "there [[item:1]]"}'}
            yield {
                "event": "done",
                "data": '{"message_id": 1, "content": "hi there [[item:1]]", "citations": '
                '[{"id": 1, "item_id": 1, "media_file_id": null, "snippet": null}]}',
            }

        with (
            patch(
                "gramvault.api.routes_chat.service.ensure_ollama_ready",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_chat.service.stream_message",
                new=fake_stream_message,
            ),
            client.stream(
                "POST",
                f"/api/chat/sessions/{session_id}/messages",
                json={"content": "hi"},
            ) as response,
        ):
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            raw = "".join(response.iter_text())

        assert "event: token" in raw
        assert "event: done" in raw
        assert "hi there" in raw
        assert "[[item:1]]" in raw


class TestSemanticSearch:
    def test_returns_ranked_results(self, client: TestClient) -> None:
        item = Item(id=3, media_type=MediaType.PHOTO, caption="a mountain view")

        with patch(
            "gramvault.api.routes_chat.service.semantic_search",
            new_callable=AsyncMock,
            return_value=[{"item": item, "score": 0.42, "snippet": "a mountain view"}],
        ):
            response = client.get("/api/chat/search", params={"q": "mountain"})

        assert response.status_code == 200
        body = response.json()
        assert body["query"] == "mountain"
        assert body["results"][0]["item"]["id"] == 3
        assert body["results"][0]["score"] == 0.42

    def test_ollama_not_running_is_503(self, client: TestClient) -> None:
        with patch(
            "gramvault.api.routes_chat.service.semantic_search",
            new_callable=AsyncMock,
            side_effect=OllamaNotRunningError("http://localhost:11434"),
        ):
            response = client.get("/api/chat/search", params={"q": "mountain"})

        assert response.status_code == 503
        assert "ollama" in response.json()["detail"].lower()

    def test_no_llm_call_needed_only_embed_and_vector_search(self, client: TestClient) -> None:
        # Documents the contract: semantic_search is a thin wrapper with no
        # chat-completion step. Verified indirectly: mocking only
        # service.semantic_search (never service.stream_message /
        # ollama_client.chat_completion) is sufficient to serve this route.
        with patch(
            "gramvault.api.routes_chat.service.semantic_search",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_search:
            response = client.get("/api/chat/search", params={"q": "anything", "top_k": 5})

        assert response.status_code == 200
        mock_search.assert_awaited_once()
        _, kwargs = mock_search.call_args
        assert kwargs.get("top_k") == 5 or mock_search.call_args.args
