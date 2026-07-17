"""GramVault CLI entry point.

Installed as the `gramvault` console script (see `pyproject.toml`
`[project.scripts]`). Subcommands are wired up here; feature agents fill
in the bodies that need real logic (import).
"""

from __future__ import annotations

from pathlib import Path

import typer

from gramvault.config import get_config
from gramvault.db.session import get_connection, init_db

app = typer.Typer(
    name="gramvault",
    help="Private, local-first library for saved Instagram content.",
    add_completion=False,
)


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Override config.yaml server.host"),
    port: int | None = typer.Option(None, help="Override config.yaml server.port"),
    reload: bool = typer.Option(False, help="Enable uvicorn auto-reload (dev only)"),
) -> None:
    """Run the FastAPI server."""
    import uvicorn

    config = get_config()
    uvicorn.run(
        "gramvault.main:app",
        host=host or config.server.host,
        port=port or config.server.port,
        reload=reload,
    )


@app.command(name="import")
def import_export(
    zip_path: Path = typer.Argument(..., help="Path to an Instagram data export ZIP file"),
) -> None:
    """Import an Instagram data export from the command line.

    TODO(A2): this should call the same ingestion logic used by
    `POST /api/import/upload` (backend/gramvault/api/routes_import.py) so
    there's a single implementation shared between the API and the CLI —
    consider factoring the actual import logic into a plain function/module
    (e.g. `gramvault.ingestion.import_zip(path, config)`) that both call.
    """
    typer.echo(f"Not implemented yet — see Agent A2 (ingestion). Would import: {zip_path}")
    raise typer.Exit(code=1)


@app.command(name="init-db")
def init_db_command() -> None:
    """Create the SQLite database and tables if they don't already exist."""
    config = get_config()
    conn = get_connection(config)
    try:
        init_db(conn)
    finally:
        conn.close()
    typer.echo(f"Database ready at {config.resolved_db_path}")


def main() -> None:
    """Console-script entry point (see pyproject.toml [project.scripts])."""
    app()


if __name__ == "__main__":
    main()
