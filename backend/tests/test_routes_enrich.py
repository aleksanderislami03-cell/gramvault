"""Tests for the `/api/enrich` HTTP surface: triggering enrichment,
aggregate progress, and per-item status — including the friendly-503
behavior when Ollama isn't reachable or the required models aren't pulled.

`gramvault.ai.pipeline.process_items` is mocked at the route-module
boundary so these tests never run the real AI pipeline; `ollama_client`'s
readiness checks are mocked directly to exercise the 503 translation path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from gramvault.ai.ollama_client import ModelNotPulledError, OllamaNotRunningError
from gramvault.config import Config
from gramvault.db.session import session_scope


def _seed_item(
    config: Config, *, media_type: str = "photo", enrichment_status: str = "pending"
) -> int:
    with session_scope(config) as conn:
        item_id = conn.execute(
            "INSERT INTO items (media_type, caption, enrichment_status) VALUES (?, ?, ?)",
            (media_type, "a caption", enrichment_status),
        ).lastrowid
    assert item_id is not None
    return item_id


class TestRunEnrichment:
    def test_triggers_pipeline_with_explicit_item_ids(
        self, client: TestClient, tmp_config: Config
    ) -> None:
        item_id = _seed_item(tmp_config)

        with (
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_running",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_model_pulled",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.pipeline.process_items", new_callable=AsyncMock
            ) as mock_process,
        ):
            response = client.post("/api/enrich/run", json={"item_ids": [item_id]})

        assert response.status_code == 202
        assert response.json()["queued_count"] == 1
        mock_process.assert_awaited_once()
        args, _kwargs = mock_process.call_args
        assert args[0] == [item_id]

    def test_none_item_ids_enqueues_all_currently_pending(
        self, client: TestClient, tmp_config: Config
    ) -> None:
        pending_id = _seed_item(tmp_config, enrichment_status="pending")
        _seed_item(tmp_config, enrichment_status="done")

        with (
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_running",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_model_pulled",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.pipeline.process_items", new_callable=AsyncMock
            ) as mock_process,
        ):
            response = client.post("/api/enrich/run", json={})

        assert response.status_code == 202
        assert response.json()["queued_count"] == 1
        args, _kwargs = mock_process.call_args
        assert args[0] == [pending_id]

    def test_explicit_item_ids_flip_status_to_pending_immediately(
        self, client: TestClient, tmp_config: Config
    ) -> None:
        item_id = _seed_item(tmp_config, enrichment_status="failed")

        with (
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_running",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_model_pulled",
                new_callable=AsyncMock,
            ),
            patch("gramvault.api.routes_enrich.pipeline.process_items", new_callable=AsyncMock),
        ):
            client.post("/api/enrich/run", json={"item_ids": [item_id]})

        with session_scope(tmp_config) as conn:
            row = conn.execute(
                "SELECT enrichment_status FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        assert row["enrichment_status"] == "pending"

    def test_no_items_to_process_returns_zero_and_skips_background_task(
        self, client: TestClient
    ) -> None:
        with (
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_running",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_model_pulled",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.pipeline.process_items", new_callable=AsyncMock
            ) as mock_process,
        ):
            response = client.post("/api/enrich/run", json={})

        assert response.status_code == 202
        assert response.json()["queued_count"] == 0
        mock_process.assert_not_awaited()

    def test_ollama_not_running_is_503_with_friendly_message_not_a_stack_trace(
        self, client: TestClient
    ) -> None:
        with patch(
            "gramvault.api.routes_enrich.ollama_client.ensure_running",
            new_callable=AsyncMock,
            side_effect=OllamaNotRunningError("http://localhost:11434"),
        ):
            response = client.post("/api/enrich/run", json={})

        assert response.status_code == 503
        body = response.json()
        assert "detail" in body
        assert "ollama serve" in body["detail"].lower()

    def test_model_not_pulled_is_503_with_friendly_message(self, client: TestClient) -> None:
        with (
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_running",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_model_pulled",
                new_callable=AsyncMock,
                side_effect=ModelNotPulledError("llava:7b"),
            ),
        ):
            response = client.post("/api/enrich/run", json={})

        assert response.status_code == 503
        body = response.json()
        assert "llava:7b" in body["detail"]
        assert "pull" in body["detail"].lower()

    def test_readiness_checks_happen_before_scheduling_background_task(
        self, client: TestClient, tmp_config: Config
    ) -> None:
        _seed_item(tmp_config)

        with (
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_running",
                new_callable=AsyncMock,
                side_effect=OllamaNotRunningError("http://localhost:11434"),
            ),
            patch(
                "gramvault.api.routes_enrich.pipeline.process_items", new_callable=AsyncMock
            ) as mock_process,
        ):
            response = client.post("/api/enrich/run", json={})

        assert response.status_code == 503
        mock_process.assert_not_awaited()

    def test_link_only_items_skip_the_vision_model_check(
        self, client: TestClient, tmp_config: Config
    ) -> None:
        # Items with no media files (link-only saved posts) enrich with just
        # the embedding model — mirrors the pipeline's per-item needs_vision
        # narrowing, so a metadata-only library works without llava pulled.
        _seed_item(tmp_config)

        with (
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_running",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_model_pulled",
                new_callable=AsyncMock,
            ) as mock_pulled,
            patch("gramvault.api.routes_enrich.pipeline.process_items", new_callable=AsyncMock),
        ):
            response = client.post("/api/enrich/run", json={})

        assert response.status_code == 202
        checked = {call.args[0] for call in mock_pulled.await_args_list}
        assert checked == {"nomic-embed-text"}

    def test_items_with_media_still_require_the_vision_model(
        self, client: TestClient, tmp_config: Config
    ) -> None:
        item_id = _seed_item(tmp_config)
        with session_scope(tmp_config) as conn:
            conn.execute(
                "INSERT INTO media_files (item_id, file_path, media_type) VALUES (?, ?, 'photo')",
                (item_id, "media/ab/abc123.jpg"),
            )

        with (
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_running",
                new_callable=AsyncMock,
            ),
            patch(
                "gramvault.api.routes_enrich.ollama_client.ensure_model_pulled",
                new_callable=AsyncMock,
            ) as mock_pulled,
            patch("gramvault.api.routes_enrich.pipeline.process_items", new_callable=AsyncMock),
        ):
            response = client.post("/api/enrich/run", json={})

        assert response.status_code == 202
        checked = {call.args[0] for call in mock_pulled.await_args_list}
        assert checked == {"llava:7b", "nomic-embed-text"}


class TestEnrichmentProgress:
    def test_aggregates_counts_by_status(self, client: TestClient, tmp_config: Config) -> None:
        _seed_item(tmp_config, enrichment_status="pending")
        _seed_item(tmp_config, enrichment_status="pending")
        _seed_item(tmp_config, enrichment_status="running")
        _seed_item(tmp_config, enrichment_status="done")
        _seed_item(tmp_config, enrichment_status="failed")

        response = client.get("/api/enrich/progress")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        assert body["pending"] == 2
        assert body["running"] == 1
        assert body["done"] == 1
        assert body["failed"] == 1

    def test_empty_library_is_all_zero(self, client: TestClient) -> None:
        response = client.get("/api/enrich/progress")

        assert response.status_code == 200
        assert response.json() == {
            "total": 0,
            "pending": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
        }


class TestItemEnrichmentStatus:
    def test_returns_item_with_status_and_media_files(
        self, client: TestClient, tmp_config: Config
    ) -> None:
        item_id = _seed_item(tmp_config, enrichment_status="done")
        with session_scope(tmp_config) as conn:
            conn.execute(
                "INSERT INTO media_files (item_id, file_path, media_type, vision_caption) "
                "VALUES (?, 'a.jpg', 'photo', ?)",
                (item_id, "a nice photo"),
            )

        response = client.get(f"/api/enrich/progress/{item_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == item_id
        assert body["enrichment_status"] == "done"
        assert body["media_files"][0]["vision_caption"] == "a nice photo"

    def test_missing_item_is_404(self, client: TestClient) -> None:
        response = client.get("/api/enrich/progress/999999")
        assert response.status_code == 404
