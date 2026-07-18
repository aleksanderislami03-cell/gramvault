"""Enrichment API (Agent A3).

Owns: triggering the AI enrichment pipeline (keyframe extraction + llava
vision captions for photos/videos, faster-whisper transcription for
videos/reels, chunking + nomic-embed-text embeddings written to ChromaDB)
and exposing progress so the frontend can poll a progress bar.

The actual pipeline logic lives in `gramvault.ai.pipeline` — this module is
just the HTTP surface: resolving the request into a target item id list,
scheduling background processing, and translating `gramvault.ai.ollama_client`'s
friendly exceptions into clean HTTP responses instead of raw stack traces.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from gramvault.ai import ollama_client, pipeline
from gramvault.api.deps import get_config_dependency
from gramvault.chat.retrieval import fetch_items
from gramvault.config import Config
from gramvault.db.session import session_scope
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
    background_tasks: BackgroundTasks,
    config: Config = Depends(get_config_dependency),
) -> EnrichmentRunResponse:
    """Enqueue items for AI enrichment.

    Checks Ollama readiness (server up + vision/embedding models pulled)
    synchronously up front, so a misconfigured setup fails fast with a
    friendly 503 instead of silently failing in the background later.
    Resolves the target item set (explicit `item_ids`, or all currently
    `pending` items), flips them to `pending` (if explicitly requested —
    so progress reflects "queued" immediately) and schedules
    `gramvault.ai.pipeline.process_items` as a background task.
    """
    try:
        await ollama_client.ensure_running(config)
        await ollama_client.ensure_model_pulled(config.models.vision_model, config)
        await ollama_client.ensure_model_pulled(config.models.embedding_model, config)
    except (ollama_client.OllamaNotRunningError, ollama_client.ModelNotPulledError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    with session_scope(config) as conn:
        item_ids = pipeline.resolve_target_item_ids(conn, body.item_ids)
        if body.item_ids is not None:
            pipeline.mark_items_pending(conn, item_ids)

    if item_ids:
        background_tasks.add_task(pipeline.process_items, item_ids, config)

    return EnrichmentRunResponse(queued_count=len(item_ids))


@router.get("/progress", response_model=EnrichmentProgress)
async def get_enrichment_progress(
    config: Config = Depends(get_config_dependency),
) -> EnrichmentProgress:
    """Aggregate enrichment progress across all items (for a global
    progress bar in the UI)."""
    with session_scope(config) as conn:
        rows = conn.execute(
            "SELECT enrichment_status, COUNT(*) AS count FROM items GROUP BY enrichment_status"
        ).fetchall()
    counts = {row["enrichment_status"]: row["count"] for row in rows}
    total = sum(counts.values())
    return EnrichmentProgress(
        total=total,
        pending=counts.get(EnrichmentStatus.PENDING.value, 0),
        running=counts.get(EnrichmentStatus.RUNNING.value, 0),
        done=counts.get(EnrichmentStatus.DONE.value, 0),
        failed=counts.get(EnrichmentStatus.FAILED.value, 0),
    )


@router.get("/progress/{item_id}", response_model=Item)
async def get_item_enrichment_status(
    item_id: int,
    config: Config = Depends(get_config_dependency),
) -> Item:
    """Fetch a single item (including `enrichment_status` and any
    populated `vision_caption`/`transcript` on its media files) — useful
    for polling one item's progress after a targeted `run_enrichment`."""
    with session_scope(config) as conn:
        items = fetch_items(conn, [item_id])
    item = items.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    return item


# Re-exported for convenience so callers can filter on status without a
# second import from gramvault.models.schemas.
__all__ = ["router", "EnrichmentStatus"]
