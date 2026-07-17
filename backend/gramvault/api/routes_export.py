"""Obsidian export API — STUB for Agent A6.

Owns: rendering library items (captions, transcripts, vision captions,
tags, media links) as Markdown notes into the user's configured Obsidian
vault (`config.paths.obsidian_vault_dir`).

Every handler below is fully signed but raises HTTP 501.

NOTE: there is no `export_jobs` table in the shared schema.sql — only
`import_jobs`. TODO(A6): if you need durable progress tracking across
restarts, add an `export_jobs` table (mirroring `import_jobs`) to
`backend/gramvault/db/schema.sql`, or extend `import_jobs` with a `kind`
column ('import' | 'export') if you'd rather reuse it. Either is fine;
just document the choice here once you decide. The `ExportJobStatus`
model below is a placeholder for whatever you land on.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gramvault.api.deps import get_config_dependency
from gramvault.config import Config
from gramvault.models.schemas import JobStatus

router = APIRouter(prefix="/api/export", tags=["export"])


class ExportRequest(BaseModel):
    # None means "export the whole library".
    item_ids: list[int] | None = None
    # Optional subfolder within the configured Obsidian vault.
    vault_subfolder: str | None = None


class ExportJobStatus(BaseModel):
    id: int
    status: JobStatus = JobStatus.PENDING
    total_items: int = 0
    processed_items: int = 0
    failed_items: int = 0
    error_message: str | None = None


@router.post("/obsidian", response_model=ExportJobStatus, status_code=202)
async def start_export(
    body: ExportRequest,
    config: Config = Depends(get_config_dependency),
) -> ExportJobStatus:
    """Kick off an export of items to the configured Obsidian vault.

    TODO(A6): implement. Expected behavior:
      1. Refuse with a clear error if `config.paths.obsidian_vault_dir`
         is unset (there's nowhere to export to).
      2. Resolve the target item set (`body.item_ids`, or the whole
         library if None).
      3. Render one Markdown note per item: caption, tags as
         Obsidian-style `#tags` or frontmatter, transcript/vision_caption
         if present, and a link/embed to the media file (copied or
         linked into the vault — your call, document it).
      4. Track progress (see module docstring re: `export_jobs`).
      5. Return the created/updated job status.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A6 (Obsidian export)")


@router.get("/jobs", response_model=list[ExportJobStatus])
async def list_export_jobs(
    config: Config = Depends(get_config_dependency),
) -> list[ExportJobStatus]:
    """List export jobs, most recent first.

    TODO(A6): depends on how you decide to persist export job state.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A6 (Obsidian export)")


@router.get("/jobs/{job_id}", response_model=ExportJobStatus)
async def get_export_job(
    job_id: int,
    config: Config = Depends(get_config_dependency),
) -> ExportJobStatus:
    """Fetch a single export job's progress (for a frontend progress bar).

    TODO(A6): return 404 (not 501) once implemented if `job_id` is missing.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A6 (Obsidian export)")
