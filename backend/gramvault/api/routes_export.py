"""Obsidian export API (Agent A6).

Owns: rendering library items (captions, transcripts, vision captions,
tags, media links) as Markdown notes into the user's configured Obsidian
vault (`config.paths.obsidian_vault_dir`).

Job tracking: a dedicated `export_jobs` table (see `db/schema.sql`) rather
than reusing `import_jobs` -- kept as its own table so we don't touch a
table Agent A2 is concurrently relying on.

Export itself runs via FastAPI `BackgroundTasks` (no separate job queue
exists in this scaffold): `POST /obsidian` creates a `pending` row and
returns immediately (202), the background task flips it to `running` then
`done`/`failed` and fills in the counts, and `GET /jobs/{id}` polls it.
Under `TestClient`, background tasks run synchronously before the request
returns, so tests can call `POST /obsidian` and immediately assert on the
final state without polling.

Actual rendering/orchestration logic lives in `gramvault.export.*`
(`markdown_builder`, `index_builder`, `repository`, `exporter`) -- this
module is just the HTTP surface + `export_jobs` persistence.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from gramvault.api.deps import get_config_dependency, get_config_path_dependency
from gramvault.config import Config, save_obsidian_vault_dir
from gramvault.db.session import session_scope
from gramvault.export.exporter import (
    ExportResult,
    VaultNotConfiguredError,
    VaultPathNotFoundError,
    export_items,
)
from gramvault.export.repository import load_items
from gramvault.models.schemas import JobStatus

router = APIRouter(prefix="/api/export", tags=["export"])


class ExportRequest(BaseModel):
    # None means "export the whole library".
    item_ids: list[int] | None = None
    # Optional subfolder within the configured Obsidian vault. Falls back
    # to `config.export.default_vault_subfolder` ("GramVault") if unset.
    vault_subfolder: str | None = None


class ExportSkippedItem(BaseModel):
    item_id: int | None
    reason: str


class ExportJobStatus(BaseModel):
    id: int
    status: JobStatus = JobStatus.PENDING
    vault_subfolder: str | None = None
    total_items: int = 0
    processed_items: int = 0
    failed_items: int = 0
    notes_written: int = 0
    notes_updated: int = 0
    media_files_copied: int = 0
    skipped: list[ExportSkippedItem] = Field(default_factory=list)
    error_message: str | None = None


class VaultPathCheckRequest(BaseModel):
    # If omitted, validates the vault path already configured in
    # config.yaml instead of a candidate path.
    vault_dir: str | None = None


class VaultPathCheckResponse(BaseModel):
    valid: bool
    reason: str | None = None


def _row_to_status(row: object) -> ExportJobStatus:
    skipped_raw = row["skipped_json"]  # type: ignore[index]
    skipped = (
        [ExportSkippedItem(**entry) for entry in json.loads(skipped_raw)] if skipped_raw else []
    )
    return ExportJobStatus(
        id=row["id"],  # type: ignore[index]
        status=JobStatus(row["status"]),  # type: ignore[index]
        vault_subfolder=row["vault_subfolder"],  # type: ignore[index]
        total_items=row["total_items"],  # type: ignore[index]
        processed_items=row["processed_items"],  # type: ignore[index]
        failed_items=row["failed_items"],  # type: ignore[index]
        notes_written=row["notes_written"],  # type: ignore[index]
        notes_updated=row["notes_updated"],  # type: ignore[index]
        media_files_copied=row["media_files_copied"],  # type: ignore[index]
        skipped=skipped,
        error_message=row["error_message"],  # type: ignore[index]
    )


def _run_export_job(
    job_id: int,
    config: Config,
    item_ids: list[int] | None,
    vault_subfolder: str | None,
) -> None:
    """Background task: perform the export and persist the outcome onto
    the `export_jobs` row. Never raises -- any failure is captured into
    the row's `error_message`/`status='failed'` so a job never gets stuck
    in `running`."""
    with session_scope(config) as conn:
        conn.execute("UPDATE export_jobs SET status = 'running' WHERE id = ?", (job_id,))

    error_message: str | None = None
    result: ExportResult | None = None
    try:
        with session_scope(config) as conn:
            items = load_items(conn, item_ids)
        result = export_items(config, items, vault_subfolder)
    except (VaultNotConfiguredError, VaultPathNotFoundError) as exc:
        error_message = str(exc)
    except Exception as exc:  # defensive: a job must never stay "running" forever
        error_message = f"Unexpected error during export: {exc}"

    with session_scope(config) as conn:
        if result is not None:
            failed = len(result.skipped)
            processed = result.notes_written + result.notes_updated
            skipped_json = json.dumps(
                [{"item_id": s.item_id, "reason": s.reason} for s in result.skipped]
            )
            conn.execute(
                """
                UPDATE export_jobs
                SET status = 'done', total_items = ?, processed_items = ?, failed_items = ?,
                    notes_written = ?, notes_updated = ?, media_files_copied = ?,
                    skipped_json = ?, finished_at = datetime('now')
                WHERE id = ?
                """,
                (
                    processed + failed,
                    processed,
                    failed,
                    result.notes_written,
                    result.notes_updated,
                    result.media_files_copied,
                    skipped_json,
                    job_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE export_jobs
                SET status = 'failed', error_message = ?, finished_at = datetime('now')
                WHERE id = ?
                """,
                (error_message, job_id),
            )


@router.post("/obsidian", response_model=ExportJobStatus, status_code=202)
async def start_export(
    body: ExportRequest,
    background_tasks: BackgroundTasks,
    config: Config = Depends(get_config_dependency),
) -> ExportJobStatus:
    """Kick off an export of items to the configured Obsidian vault.

    Refuses immediately (400) if the vault isn't configured, or is
    configured but doesn't exist on disk -- we never silently create an
    arbitrary folder tree outside a vault path the user actually pointed
    us at. The target *subfolder* inside the vault is created
    automatically if missing.
    """
    vault_dir = config.resolved_obsidian_vault_dir
    if vault_dir is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Obsidian vault configured. Set paths.obsidian_vault_dir in "
                "config.yaml to the root folder of an existing Obsidian vault."
            ),
        )
    if not vault_dir.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Configured Obsidian vault folder does not exist: {vault_dir}",
        )

    with session_scope(config) as conn:
        cursor = conn.execute(
            "INSERT INTO export_jobs (vault_subfolder, status) VALUES (?, 'pending')",
            (body.vault_subfolder,),
        )
        job_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM export_jobs WHERE id = ?", (job_id,)).fetchone()

    background_tasks.add_task(_run_export_job, job_id, config, body.item_ids, body.vault_subfolder)
    return _row_to_status(row)


@router.get("/jobs", response_model=list[ExportJobStatus])
async def list_export_jobs(
    config: Config = Depends(get_config_dependency),
) -> list[ExportJobStatus]:
    """List export jobs, most recent first."""
    with session_scope(config) as conn:
        rows = conn.execute("SELECT * FROM export_jobs ORDER BY id DESC").fetchall()
    return [_row_to_status(row) for row in rows]


@router.get("/jobs/{job_id}", response_model=ExportJobStatus)
async def get_export_job(
    job_id: int,
    config: Config = Depends(get_config_dependency),
) -> ExportJobStatus:
    """Fetch a single export job's progress (for a frontend progress bar)."""
    with session_scope(config) as conn:
        row = conn.execute("SELECT * FROM export_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Export job {job_id} not found")
    return _row_to_status(row)


@router.post("/validate-vault", response_model=VaultPathCheckResponse)
async def validate_vault_path(
    body: VaultPathCheckRequest,
    config: Config = Depends(get_config_dependency),
) -> VaultPathCheckResponse:
    """Check whether a vault path (or the currently configured one, if
    `vault_dir` is omitted) exists and is a directory, without performing
    an export. Useful for a frontend settings form to validate before
    saving/exporting."""
    candidate = body.vault_dir if body.vault_dir is not None else (
        str(config.paths.obsidian_vault_dir) if config.paths.obsidian_vault_dir else None
    )
    if not candidate:
        return VaultPathCheckResponse(valid=False, reason="No vault path provided or configured")

    path = Path(candidate).expanduser()
    if not path.exists():
        return VaultPathCheckResponse(valid=False, reason=f"Path does not exist: {path}")
    if not path.is_dir():
        return VaultPathCheckResponse(valid=False, reason=f"Path is not a directory: {path}")
    return VaultPathCheckResponse(valid=True, reason=None)


@router.post("/vault-path", response_model=VaultPathCheckResponse)
async def save_vault_path(
    body: VaultPathCheckRequest,
    config_path: Path = Depends(get_config_path_dependency),
) -> VaultPathCheckResponse:
    """Persist a vault path into config.yaml (`paths.obsidian_vault_dir`) so
    a subsequent `POST /obsidian` (which only ever reads from config, never
    from its own request body) actually exports there. Pass `vault_dir: null`
    to clear it. Validates the path exists before saving."""
    vault_dir = body.vault_dir.strip() if body.vault_dir else None
    if vault_dir:
        path = Path(vault_dir).expanduser()
        if not path.is_dir():
            raise HTTPException(
                status_code=400, detail=f"Path does not exist or is not a directory: {path}"
            )
        vault_dir = str(path.resolve())
    save_obsidian_vault_dir(vault_dir, config_path=config_path)
    return VaultPathCheckResponse(valid=True, reason=None)
