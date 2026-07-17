"""Shared pytest fixtures for GramVault's backend test suite.

pytest auto-discovers this conftest.py, so `tmp_config`, `tmp_db_conn`,
and `client` are available to any test under backend/tests/ (and to
A2-A6's own test modules once they add them here) without an import.
Add feature-specific fixtures in your own test files; only put things
here that are genuinely shared across feature areas.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gramvault.api.deps import get_config_dependency
from gramvault.config import Config, PathsConfig
from gramvault.db.session import get_connection, init_db
from gramvault.main import create_app


@pytest.fixture
def tmp_config(tmp_path: Path) -> Config:
    """A Config pointed entirely at a pytest tmp_path — safe to read/write
    without touching the real library/db/chroma directories."""
    return Config(
        paths=PathsConfig(
            library_dir=str(tmp_path / "library"),
            db_path=str(tmp_path / "data" / "gramvault.db"),
            chroma_dir=str(tmp_path / "data" / "chroma"),
            obsidian_vault_dir=None,
        )
    )


@pytest.fixture
def tmp_db_conn(tmp_config: Config) -> Iterator[sqlite3.Connection]:
    """A live SQLite connection with the schema already applied, backed by
    tmp_config's db_path. Closed automatically after the test."""
    conn = get_connection(tmp_config)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client(tmp_config: Config) -> Iterator[TestClient]:
    """A TestClient for the FastAPI app, wired to use tmp_config instead
    of the real (cwd-relative) configuration. Use
    `app.dependency_overrides` (already set up here for
    `get_config_dependency`) as the pattern for overriding any other
    per-request dependency your feature area adds."""
    app = create_app(tmp_config)
    app.dependency_overrides[get_config_dependency] = lambda: tmp_config
    with TestClient(app) as test_client:
        yield test_client
