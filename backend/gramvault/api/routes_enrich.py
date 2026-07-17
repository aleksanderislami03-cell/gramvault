"""Enrichment API — STUB for Agent A3.

Owns: triggering the AI enrichment pipeline (keyframe extraction + llava
vision captions for photos/videos, faster-whisper transcription for
videos/reels, chunking + nomic-embed-text embeddings written to ChromaDB)
and exposing progress so the frontend can poll a progress bar.

Every handler below is fully signed but raises HTTP 501.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gramvault.api.deps import get_config_dependency
from gramvault.config import Config
from gramvault.models.schemas import EnrichmentStatus, Item

router = APIRouter(prefix="/api/enrich", tags=["enrich"])


class EnrichmentRunRequest(BaseModel):
    # None means "enqueue all items currently pending enrichment".
    item_ids: list[int] | None = None


class EnrichmentRunResponse(BaseModel):
    queued_count: int


class EnrichmentProgress(BaseModel):
    total: int
    pending: int
    running: int
    done: int
    failed: int


@router.post("/run", response_model=EnrichmentRunResponse, status_code=202)
async def run_enrichment(
    body: EnrichmentRunRequest,
    config: Config = Depends(get_config_dependency),
) -> EnrichmentRunResponse:
    """Enqueue items for AI enrichment.

    TODO(A3): implement. Expected behavior:
      1. Resolve the target item set (`body.item_ids`, or all items with
         `enrichment_status='pending'` if None).
      2. Flip each target item's `enrichment_status` to 'running' and
         enqueue processing (background task/queue — your call).
      3. Pipeline per item (see config.yaml `video.*` for keyframe
         settings and `models.*` for model names — call Ollama, don't
         hardcode model names):
           - photo: llava vision caption -> MediaFile.vision_caption
           - video/reel: ffmpeg keyframes (video.keyframe_interval_seconds,
             video.max_keyframes) -> llava captions; faster-whisper audio
             transcription -> MediaFile.transcript
           - chunk caption+transcript+vision_caption text
             (chunking.chunk_size/chunk_overlap) and embed with
             nomic-embed-text into ChromaDB (config.resolved_chroma_dir),
             keyed so Agent A4 can look up the source item/media_file.
      4. Set `enrichment_status` to 'done' or 'failed' when finished.
      5. Return how many items were queued.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A3 (AI pipeline)")


@router.get("/progress", response_model=EnrichmentProgress)
async def get_enrichment_progress(
    config: Config = Depends(get_config_dependency),
) -> EnrichmentProgress:
    """Aggregate enrichment progress across all items (for a global
    progress bar in the UI).

    TODO(A3): `SELECT enrichment_status, COUNT(*) FROM items GROUP BY
    enrichment_status` and shape it into `EnrichmentProgress`.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A3 (AI pipeline)")


@router.get("/progress/{item_id}", response_model=Item)
async def get_item_enrichment_status(
    item_id: int,
    config: Config = Depends(get_config_dependency),
) -> Item:
    """Fetch a single item (including `enrichment_status` and any
    populated `vision_caption`/`transcript` on its media files) — useful
    for polling one item's progress after a targeted `run_enrichment`.

    TODO(A3): return 404 (not 501) once implemented if `item_id` is missing.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A3 (AI pipeline)")


# Re-exported for convenience so callers can filter on status without a
# second import from gramvault.models.schemas.
__all__ = ["router", "EnrichmentStatus"]
