"""FastAPI application factory for GramVault.

Wires together all feature routers (see `gramvault.api`) and, in
production, serves the built frontend (`frontend/dist/`) as static files.

Run via `gramvault serve` (see `gramvault.cli`) or directly with uvicorn:
    uvicorn gramvault.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from gramvault.api import routes_chat, routes_enrich, routes_export, routes_import, routes_library
from gramvault.config import Config, get_config
from gramvault.db.session import get_connection, init_db

# Vite's default dev server origin — allowed for local frontend development.
# TODO(A5): adjust/remove once the real frontend dev workflow is settled.
_DEV_FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# Where `gramvault.cli`'s frontend build step is expected to output static
# assets. TODO(A5): confirm this matches your Vite `build.outDir`.
_FRONTEND_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(config: Config | None = None) -> FastAPI:
    """Build and return a configured FastAPI app instance.

    Accepting an optional `config` (rather than always calling
    `get_config()` internally) makes it easy for tests to construct an
    app pointed at a temp config/db.
    """
    config = config or get_config()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Make sure the SQLite schema exists before serving requests.
        # Cheap/idempotent (CREATE TABLE IF NOT EXISTS) so this is safe to
        # run on every startup.
        conn = get_connection(config)
        try:
            init_db(conn)
        finally:
            conn.close()
        yield

    app = FastAPI(
        title="GramVault",
        description="Private, local-first library for saved Instagram content.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.config = config

    # --- feature routers (all currently stubs — see api/routes_*.py) ---
    app.include_router(routes_import.router)
    app.include_router(routes_library.router)
    app.include_router(routes_enrich.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_export.router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- serve the built frontend in production, if present ---
    # TODO(A5): this expects a Vite production build at frontend/dist/
    # with an index.html entrypoint. Until A5 builds the real frontend,
    # this directory won't exist and the mount is skipped (API-only mode).
    if _FRONTEND_DIST_DIR.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(_FRONTEND_DIST_DIR), html=True),
            name="frontend",
        )

    return app


app = create_app()
