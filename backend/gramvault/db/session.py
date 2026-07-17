"""SQLite connection/session management.

Design choice — plain sqlite3 + schema.sql, not an ORM:
    GramVault is a single-user, local-first app with a modest, stable
    schema. We chose the stdlib `sqlite3` module with a hand-written
    `schema.sql` (CREATE TABLE IF NOT EXISTS) instead of SQLAlchemy or
    another ORM, to keep the dependency footprint small — this project
    already pulls in heavy AI dependencies (chromadb, faster-whisper,
    etc.) elsewhere. Agents may issue raw SQL via the `sqlite3.Connection`
    returned by `get_connection()` / `session_scope()`.

    If a future agent decides an ORM is warranted after all, swap this
    module's internals but keep the public functions
    (`get_connection`, `init_db`, `session_scope`, `get_db_path`) stable
    so callers don't need to change.

Migration approach: see the comment at the top of schema.sql.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from gramvault.config import Config, get_config

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_db_path(config: Config | None = None) -> Path:
    """Resolve the configured SQLite database file path."""
    config = config or get_config()
    return config.resolved_db_path


def get_connection(config: Config | None = None) -> sqlite3.Connection:
    """Open a new SQLite connection, creating parent directories as needed.

    Callers are responsible for closing the connection (or use
    `session_scope()` below for automatic commit/rollback/close).
    """
    db_path = get_db_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply schema.sql (idempotent: CREATE TABLE IF NOT EXISTS)."""
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


@contextmanager
def session_scope(config: Config | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager: opens a connection, ensures the schema exists,
    commits on clean exit, rolls back on exception, always closes.

    Example:
        with session_scope() as conn:
            conn.execute("INSERT INTO authors (username) VALUES (?)", ("alice",))
    """
    conn = get_connection(config)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
