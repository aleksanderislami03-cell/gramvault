"""Tests for the /api/export/* HTTP surface (Agent A6).

Uses its own `export_client` fixture (rather than conftest's `client`)
because these tests need `paths.obsidian_vault_dir` actually configured
and pointed at a tmp_path vault folder — conftest's shared `tmp_config`
deliberately leaves it unset for the other feature areas' tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from gramvault.api.deps import get_config_dependency, get_config_path_dependency
from gramvault.config import Config, PathsConfig
from gramvault.db.session import session_scope
from gramvault.main import create_app


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    path = tmp_path / "vault"
    path.mkdir()
    return path


@pytest.fixture
def export_config(tmp_path: Path, vault_dir: Path) -> Config:
    return Config(
        paths=PathsConfig(
            library_dir=str(tmp_path / "library"),
            db_path=str(tmp_path / "data" / "gramvault.db"),
            chroma_dir=str(tmp_path / "data" / "chroma"),
            obsidian_vault_dir=str(vault_dir),
        )
    )


@pytest.fixture
def export_client(export_config: Config) -> Iterator[TestClient]:
    app = create_app(export_config)
    app.dependency_overrides[get_config_dependency] = lambda: export_config
    with TestClient(app) as test_client:
        yield test_client


def _seed_one_item(config: Config) -> int:
    """Insert a minimal author + item directly via SQL (not through A2's
    still-stubbed import pipeline) and return the new item's id."""
    with session_scope(config) as conn:
        author_id = conn.execute(
            "INSERT INTO authors (username) VALUES (?)", ("test-author",)
        ).lastrowid
        item_id = conn.execute(
            """
            INSERT INTO items (external_id, author_id, media_type, caption, permalink)
            VALUES (?, ?, 'photo', ?, ?)
            """,
            ("shortcode123", author_id, "A test caption", "https://instagram.com/p/shortcode123/"),
        ).lastrowid
    return item_id


class TestStartExportValidation:
    def test_vault_not_configured_returns_400(self, tmp_path: Path) -> None:
        config = Config(
            paths=PathsConfig(
                library_dir=str(tmp_path / "library"),
                db_path=str(tmp_path / "data" / "gramvault.db"),
                chroma_dir=str(tmp_path / "data" / "chroma"),
                obsidian_vault_dir=None,
            )
        )
        app = create_app(config)
        app.dependency_overrides[get_config_dependency] = lambda: config
        with TestClient(app) as client:
            response = client.post("/api/export/obsidian", json={})
        assert response.status_code == 400

    def test_nonexistent_vault_path_returns_400_with_friendly_message(
        self, tmp_path: Path
    ) -> None:
        missing_vault = tmp_path / "nowhere"
        config = Config(
            paths=PathsConfig(
                library_dir=str(tmp_path / "library"),
                db_path=str(tmp_path / "data" / "gramvault.db"),
                chroma_dir=str(tmp_path / "data" / "chroma"),
                obsidian_vault_dir=str(missing_vault),
            )
        )
        app = create_app(config)
        app.dependency_overrides[get_config_dependency] = lambda: config
        with TestClient(app) as client:
            response = client.post("/api/export/obsidian", json={})
        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"]


class TestExportEndToEnd:
    def test_export_whole_library_writes_note(
        self, export_client: TestClient, export_config: Config, vault_dir: Path
    ) -> None:
        item_id = _seed_one_item(export_config)

        response = export_client.post("/api/export/obsidian", json={})
        assert response.status_code == 202
        job = response.json()
        assert job["status"] in ("pending", "running", "done")

        # TestClient runs BackgroundTasks synchronously before returning,
        # so the job should already be done by the time we poll it.
        job_response = export_client.get(f"/api/export/jobs/{job['id']}")
        assert job_response.status_code == 200
        job_status = job_response.json()
        assert job_status["status"] == "done"
        assert job_status["notes_written"] == 1
        assert job_status["notes_updated"] == 0
        assert job_status["failed_items"] == 0

        note_files = [
            p for p in (vault_dir / "GramVault").glob("*.md") if p.name != "GramVault Index.md"
        ]
        assert len(note_files) == 1
        text = note_files[0].read_text(encoding="utf-8")
        assert f"gramvault_id: {item_id}" in text
        assert "A test caption" in text

    def test_reexport_updates_not_duplicates(
        self, export_client: TestClient, export_config: Config, vault_dir: Path
    ) -> None:
        _seed_one_item(export_config)

        first = export_client.post("/api/export/obsidian", json={})
        export_client.get(f"/api/export/jobs/{first.json()['id']}")

        second = export_client.post("/api/export/obsidian", json={})
        second_job = export_client.get(f"/api/export/jobs/{second.json()['id']}").json()
        assert second_job["status"] == "done"
        assert second_job["notes_written"] == 0
        assert second_job["notes_updated"] == 1

        note_files = [
            p for p in (vault_dir / "GramVault").glob("*.md") if p.name != "GramVault Index.md"
        ]
        assert len(note_files) == 1

    def test_export_specific_item_ids(
        self, export_client: TestClient, export_config: Config
    ) -> None:
        item_id = _seed_one_item(export_config)
        response = export_client.post("/api/export/obsidian", json={"item_ids": [item_id]})
        job = export_client.get(f"/api/export/jobs/{response.json()['id']}").json()
        assert job["status"] == "done"
        assert job["notes_written"] == 1

    def test_export_nonexistent_item_ids_skips_gracefully(
        self, export_client: TestClient, export_config: Config
    ) -> None:
        response = export_client.post("/api/export/obsidian", json={"item_ids": [99999]})
        job = export_client.get(f"/api/export/jobs/{response.json()['id']}").json()
        assert job["status"] == "done"
        assert job["notes_written"] == 0
        assert job["failed_items"] == 0  # nonexistent id just means an empty result set


class TestJobListing:
    def test_list_jobs_most_recent_first(
        self, export_client: TestClient, export_config: Config
    ) -> None:
        _seed_one_item(export_config)
        first = export_client.post("/api/export/obsidian", json={}).json()
        second = export_client.post("/api/export/obsidian", json={}).json()

        jobs = export_client.get("/api/export/jobs").json()
        ids = [job["id"] for job in jobs]
        assert ids.index(second["id"]) < ids.index(first["id"])

    def test_get_missing_job_returns_404(self, export_client: TestClient) -> None:
        response = export_client.get("/api/export/jobs/999999")
        assert response.status_code == 404


class TestValidateVault:
    def test_valid_existing_directory(self, export_client: TestClient, vault_dir: Path) -> None:
        response = export_client.post("/api/export/validate-vault", json={"vault_dir": str(vault_dir)})
        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_nonexistent_directory_is_invalid(
        self, export_client: TestClient, tmp_path: Path
    ) -> None:
        response = export_client.post(
            "/api/export/validate-vault", json={"vault_dir": str(tmp_path / "nope")}
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False

    def test_file_path_is_invalid(self, export_client: TestClient, tmp_path: Path) -> None:
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("hello")
        response = export_client.post(
            "/api/export/validate-vault", json={"vault_dir": str(file_path)}
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False

    def test_falls_back_to_configured_vault_when_omitted(
        self, export_client: TestClient, vault_dir: Path
    ) -> None:
        response = export_client.post("/api/export/validate-vault", json={})
        assert response.status_code == 200
        assert response.json()["valid"] is True


@pytest.fixture
def config_yaml_file(tmp_path: Path) -> Path:
    """A standalone config.yaml on disk, isolated from the real project's
    config.yaml, for tests that exercise save_vault_path's file-writing."""
    path = tmp_path / "config.yaml"
    path.write_text("paths:\n  obsidian_vault_dir: null\n# a user comment\n", encoding="utf-8")
    return path


@pytest.fixture
def export_client_with_config_file(
    export_config: Config, config_yaml_file: Path
) -> Iterator[TestClient]:
    """Like `export_client`, but also overrides `get_config_path_dependency`
    to a tmp config.yaml so save-vault-path tests never touch the real
    project's config.yaml on disk."""
    app = create_app(export_config)
    app.dependency_overrides[get_config_dependency] = lambda: export_config
    app.dependency_overrides[get_config_path_dependency] = lambda: config_yaml_file
    with TestClient(app) as test_client:
        yield test_client


class TestSaveVaultPath:
    def test_saves_valid_path_and_preserves_comments(
        self, export_client_with_config_file: TestClient, config_yaml_file: Path, vault_dir: Path
    ) -> None:
        response = export_client_with_config_file.post(
            "/api/export/vault-path", json={"vault_dir": str(vault_dir)}
        )
        assert response.status_code == 200
        assert response.json()["valid"] is True

        text = config_yaml_file.read_text(encoding="utf-8")
        parsed = yaml.safe_load(text)
        assert parsed["paths"]["obsidian_vault_dir"] == str(vault_dir.resolve())
        assert "# a user comment" in text  # targeted rewrite, not a full YAML dump

    def test_rejects_nonexistent_directory_without_writing(
        self, export_client_with_config_file: TestClient, config_yaml_file: Path, tmp_path: Path
    ) -> None:
        original = config_yaml_file.read_text(encoding="utf-8")
        response = export_client_with_config_file.post(
            "/api/export/vault-path", json={"vault_dir": str(tmp_path / "nope")}
        )
        assert response.status_code == 400
        assert config_yaml_file.read_text(encoding="utf-8") == original

    def test_null_clears_the_configured_path(
        self, export_client_with_config_file: TestClient, config_yaml_file: Path, vault_dir: Path
    ) -> None:
        export_client_with_config_file.post("/api/export/vault-path", json={"vault_dir": str(vault_dir)})

        response = export_client_with_config_file.post("/api/export/vault-path", json={"vault_dir": None})
        assert response.status_code == 200

        text = config_yaml_file.read_text(encoding="utf-8")
        assert "obsidian_vault_dir: null" in text
        assert response.status_code == 200
        assert response.json()["valid"] is True
