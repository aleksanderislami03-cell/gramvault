"""Import/ingestion API — STUB for Agent A2.

Owns: uploading an Instagram data export ZIP, unpacking it, creating
authors/items/media_files rows, and tracking progress via `import_jobs`
so a failed/interrupted import can resume rather than restart.

Every handler below is fully signed (path, method, request/response
models) but raises HTTP 501. Fill in the bodies; do not change the
signatures without updating Agent A5 (frontend) and this docstring.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from gramvault.api.deps import get_config_dependency
from gramvault.config import Config
from gramvault.models.schemas import ImportJob

router = APIRouter(prefix="/api/import", tags=["import"])


class ImportJobListResponse(BaseModel):
    jobs: list[ImportJob]


@router.post("/upload", response_model=ImportJob, status_code=202)
async def upload_export(
    file: UploadFile = File(..., description="Instagram data export ZIP file"),
    config: Config = Depends(get_config_dependency),
) -> ImportJob:
    """Upload an Instagram export ZIP and start an import job.

    TODO(A2): Implement this. Expected behavior:
      1. Save/stream `file` into a temp location (do not trust its name).
      2. Validate it looks like an Instagram export (expected top-level
         folders/JSON files) before committing to a full unzip.
      3. Create an `import_jobs` row (status=pending) via
         `gramvault.db.session.session_scope()`.
      4. Kick off processing (sync or background task/queue — your call)
         that unpacks media into `config.resolved_library_dir`, creates
         `authors`/`items`/`media_files` rows, and updates
         `total_items`/`processed_items`/`failed_items` as it goes so
         `GET /api/import/jobs/{id}` reflects live progress.
      5. Must be resumable: if interrupted, a retry should skip items
         already recorded rather than re-processing them.
      6. Return the created ImportJob (status=pending or running).
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A2 (ingestion)")


@router.get("/jobs", response_model=ImportJobListResponse)
async def list_import_jobs(
    config: Config = Depends(get_config_dependency),
) -> ImportJobListResponse:
    """List all import jobs, most recent first.

    TODO(A2): query the `import_jobs` table and return them ordered by
    `created_at DESC`.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A2 (ingestion)")


@router.get("/jobs/{job_id}", response_model=ImportJob)
async def get_import_job(
    job_id: int,
    config: Config = Depends(get_config_dependency),
) -> ImportJob:
    """Fetch a single import job's current status/progress.

    TODO(A2): used by the frontend to poll progress after `upload_export`.
    Return 404 (not 501) if `job_id` doesn't exist once implemented.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A2 (ingestion)")


@router.post("/jobs/{job_id}/cancel", response_model=ImportJob)
async def cancel_import_job(
    job_id: int,
    config: Config = Depends(get_config_dependency),
) -> ImportJob:
    """Request cancellation of an in-progress import job.

    TODO(A2): mark the job for cancellation (e.g. a `status='failed'` with
    `error_message='cancelled by user'`, or add a dedicated status if you
    prefer) and make the background worker check for it cooperatively.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A2 (ingestion)")
