"""Import/ingestion API (Agent A2).

Owns: uploading an Instagram data export ZIP, unpacking it, creating
authors/items/media_files rows, and tracking progress via `import_jobs`
so a failed/interrupted import can resume rather than restart.

The actual parsing/organizing/DB-writing logic lives in
`gramvault.ingestion` (shared with the `gramvault import` CLI command —
see `gramvault.cli.import_export`) so there's exactly one implementation
behind both entry points.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from gramvault.api.deps import get_config_dependency
from gramvault.config import Config
from gramvault.ingestion import importer
from gramvault.ingestion.parser import ExportFormatError
from gramvault.models.schemas import ImportJob, JobStatus

router = APIRouter(prefix="/api/import", tags=["import"])


class ImportJobListResponse(BaseModel):
    jobs: list[ImportJob]


@router.post("/upload", response_model=ImportJob, status_code=202)
async def upload_export(
    file: UploadFile = File(..., description="Instagram data export ZIP file"),
    config: Config = Depends(get_config_dependency),
) -> ImportJob:
    """Upload an Instagram export ZIP and run the import.

    The upload is saved under `<library_dir>/imports/` (never trusting
    the client-supplied filename beyond its basename) before parsing
    starts. Import runs synchronously for v1 — a saved-posts+media ZIP
    processes fast enough that a background queue isn't worth the extra
    moving parts yet; A3 owns the (separately long-running) enrichment
    background queue.

    A ZIP that doesn't look like a real Instagram export doesn't raise an
    HTTP error here — it comes back as a normal `ImportJob` with
    `status="failed"` and a friendly `error_message`, so the frontend can
    show it inline rather than having to special-case a 4xx/5xx.
    """
    safe_name = Path(file.filename or "export.zip").name or "export.zip"
    imports_dir = config.resolved_library_dir / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    dest = imports_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    await file.close()

    job = importer.create_import_job(dest, config)
    assert job.id is not None
    try:
        job = importer.run_import(job.id, dest, config)
    except Exception as exc:  # pragma: no cover - defensive catch-all
        job = importer.fail_job(job.id, f"Unexpected error during import: {exc}", config)
    return job


@router.get("/jobs", response_model=ImportJobListResponse)
async def list_import_jobs(
    config: Config = Depends(get_config_dependency),
) -> ImportJobListResponse:
    """List all import jobs, most recent first."""
    return ImportJobListResponse(jobs=importer.list_import_jobs(config))


@router.get("/jobs/{job_id}", response_model=ImportJob)
async def get_import_job(
    job_id: int,
    config: Config = Depends(get_config_dependency),
) -> ImportJob:
    """Fetch a single import job's current status/progress. Used by the
    frontend to poll progress after `upload_export`."""
    job = importer.get_import_job(job_id, config)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Import job {job_id} not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=ImportJob)
async def cancel_import_job(
    job_id: int,
    config: Config = Depends(get_config_dependency),
) -> ImportJob:
    """Request cancellation of an in-progress import job.

    Import runs synchronously in v1, so in practice a job is almost
    always already `done`/`failed` by the time this can be called — it's
    a no-op in that case. Kept as a real endpoint (rather than removed)
    so the frontend and a future background-queue version both have a
    stable contract to call.
    """
    job = importer.cancel_import_job(job_id, config)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Import job {job_id} not found")
    return job


# Re-exported for readability at call sites that only need the enum, e.g.
# tests asserting on job.status without importing gramvault.models.schemas.
__all__ = ["router", "ExportFormatError", "JobStatus"]
