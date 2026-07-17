"""Smoke tests for the scaffold itself: package imports, app creation, the
health check, DB schema application, and that every stub route is wired
up correctly (returns 501 rather than 404) so A2-A6 can build on them.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import gramvault
from gramvault.config import Config
from gramvault.db.session import session_scope


def test_package_imports() -> None:
    assert gramvault.__version__


def test_health_check(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_db_schema_applies(tmp_db_conn) -> None:
    tables = {
        row["name"]
        for row in tmp_db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    expected = {
        "authors",
        "items",
        "media_files",
        "tags",
        "item_tags",
        "import_jobs",
        "chat_sessions",
        "chat_messages",
        "chat_citations",
    }
    assert expected.issubset(tables)


def test_session_scope_context_manager(tmp_config: Config) -> None:
    with session_scope(tmp_config) as conn:
        conn.execute("INSERT INTO authors (username) VALUES (?)", ("someone",))
    with session_scope(tmp_config) as conn:
        row = conn.execute(
            "SELECT username FROM authors WHERE username = ?", ("someone",)
        ).fetchone()
    assert row["username"] == "someone"


def test_stub_routes_return_501(client: TestClient) -> None:
    # A representative sample of stub endpoints across each feature area —
    # proves the routers are mounted and signatures are valid, without
    # needing real implementations yet.
    assert client.get("/api/import/jobs").status_code == 501
    assert client.get("/api/library/items").status_code == 501
    assert client.get("/api/enrich/progress").status_code == 501
    assert client.get("/api/chat/search", params={"q": "test"}).status_code == 501
    assert client.post("/api/export/obsidian", json={}).status_code == 501
