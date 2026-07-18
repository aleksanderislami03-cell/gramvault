"""Per-item AI enrichment orchestration + a simple in-process job "queue".

Pipeline, per item:
    1. For each of the item's media files:
         - photo: caption directly with the vision model (llava).
         - video: extract up to `video.max_keyframes` keyframes (ffmpeg,
           one every `video.keyframe_interval_seconds` seconds), caption
           each with the vision model and join them into one
           `vision_caption` string; separately transcribe the audio track
           with faster-whisper into `transcript`.
       Fields that are already populated are left untouched — this is
       what makes a restart resumable: re-running a partially-enriched
       item only performs the remaining steps instead of redoing
       everything (there's no separate "captioning/transcribing/embedding"
       sub-status column; the presence of `vision_caption`/`transcript`
       on each `media_files` row already encodes that state without
       needing to touch the shared `items.enrichment_status` CHECK
       constraint, which only allows pending/running/done/failed).
    2. Build one combined "content document" for the item (author,
       original caption, tags, per-media captions/transcripts) via
       `document_builder`.
    3. Chunk it (`chunking.chunk_size`/`chunk_overlap`) and embed each
       chunk with the configured embedding model (nomic-embed-text via
       Ollama), upserting into ChromaDB via `embedding_store`.
    4. Flip `items.enrichment_status` to 'done', or 'failed' with the
       exception message recorded in `items.raw_metadata_json` (merged in
       as `{"enrichment_error": "..."}`, preserving whatever was already
       in that column) if anything raised.

Job "queue": `items.enrichment_status` IS the queue. `process_items()` is
handed a list of item ids and processes them sequentially; routes_enrich.py
schedules it as a FastAPI `BackgroundTasks` callback rather than running a
separate worker thread/process, per the "keep it simple, no Celery/Redis"
brief. Because progress lives in SQLite, a crash/restart mid-run just
leaves some items at 'running' (or 'pending') to be picked up again by the
next `/api/enrich/run` call — no additional state to reconcile.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

from gramvault.ai import document_builder, embedding_store, keyframes, ollama_client, transcription
from gramvault.chat.retrieval import fetch_items
from gramvault.config import Config, get_config
from gramvault.db.session import session_scope
from gramvault.models.schemas import EnrichmentStatus, Item, MediaFile

logger = logging.getLogger(__name__)


# --- path helpers ------------------------------------------------------------


def _resolve_media_path(config: Config, file_path: str) -> Path:
    """`media_files.file_path` may be stored absolute or relative to the
    configured library directory — resolve either form consistently."""
    path = Path(file_path)
    if path.is_absolute():
        return path
    return config.resolved_library_dir / path


def _keyframes_dir(config: Config, media_file_id: int) -> Path:
    """Scratch directory for extracted video keyframes, alongside the
    SQLite DB / Chroma dir under the configured `paths.db_path`'s parent
    (so everything the app writes lives under one `data/` root)."""
    return config.resolved_db_path.parent / "keyframes" / str(media_file_id)


# --- SQLite update helpers ----------------------------------------------------


def _update_media_file(
    conn: sqlite3.Connection,
    media_file_id: int,
    *,
    vision_caption: str | None = None,
    transcript: str | None = None,
) -> None:
    if vision_caption is not None:
        conn.execute(
            "UPDATE media_files SET vision_caption = ? WHERE id = ?",
            (vision_caption, media_file_id),
        )
    if transcript is not None:
        conn.execute(
            "UPDATE media_files SET transcript = ? WHERE id = ?",
            (transcript, media_file_id),
        )


def _merge_raw_metadata(conn: sqlite3.Connection, item_id: int, updates: dict) -> None:
    """Merge `updates` into `items.raw_metadata_json` (a free-form JSON blob
    already in the shared schema for "anything unmapped"), preserving keys
    not touched here. `updates[key] = None` deletes that key."""
    row = conn.execute("SELECT raw_metadata_json FROM items WHERE id = ?", (item_id,)).fetchone()
    raw: dict = {}
    if row and row["raw_metadata_json"]:
        try:
            raw = json.loads(row["raw_metadata_json"])
        except (json.JSONDecodeError, TypeError):
            raw = {}
    for key, value in updates.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    conn.execute(
        "UPDATE items SET raw_metadata_json = ? WHERE id = ?", (json.dumps(raw), item_id)
    )


def _set_item_status(
    conn: sqlite3.Connection, item_id: int, status: EnrichmentStatus, error: str | None = None
) -> None:
    conn.execute("UPDATE items SET enrichment_status = ? WHERE id = ?", (status.value, item_id))
    if status == EnrichmentStatus.FAILED and error is not None:
        _merge_raw_metadata(conn, item_id, {"enrichment_error": error})
    elif status == EnrichmentStatus.DONE:
        # Clear any stale error recorded by a previous failed attempt.
        _merge_raw_metadata(conn, item_id, {"enrichment_error": None})


# --- per-media-file steps -----------------------------------------------------


async def _caption_photo(config: Config, media_file: MediaFile) -> str:
    path = _resolve_media_path(config, media_file.file_path)
    return await ollama_client.caption_image(path, model=config.models.vision_model, config=config)


async def _caption_video(config: Config, media_file: MediaFile) -> str:
    assert media_file.id is not None
    path = _resolve_media_path(config, media_file.file_path)
    out_dir = _keyframes_dir(config, media_file.id)
    frames = await asyncio.to_thread(keyframes.extract_keyframes, path, out_dir, config)
    if not frames:
        return ""
    captions: list[str] = []
    for frame in frames:
        caption = await ollama_client.caption_image(
            frame, model=config.models.vision_model, config=config
        )
        if caption:
            captions.append(caption.strip())
    return " | ".join(captions)


async def _transcribe_video(config: Config, media_file: MediaFile) -> str:
    path = _resolve_media_path(config, media_file.file_path)
    result = await asyncio.to_thread(transcription.transcribe, path)
    return result.text


# --- embedding -----------------------------------------------------------------


async def _embed_and_upsert(config: Config, item: Item) -> None:
    document = document_builder.build_content_document(item)
    if not document:
        return
    chunks = document_builder.chunk_text(
        document, config.chunking.chunk_size, config.chunking.chunk_overlap
    )
    for index, chunk in enumerate(chunks):
        embedding = await ollama_client.embed(
            chunk, model=config.models.embedding_model, config=config
        )
        await asyncio.to_thread(
            embedding_store.upsert_item,
            item.id,
            embedding,
            chunk,
            None,  # media_file_id: this document merges all of the item's media
            index,
            {"media_type": str(item.media_type)},
            config,
        )


# --- per-item orchestration ----------------------------------------------------


async def process_item(item_id: int, config: Config | None = None) -> None:
    """Run the full enrichment pipeline for one item, resumably (see
    module docstring). Never raises — failures are recorded on the item
    (`enrichment_status='failed'` + `raw_metadata_json.enrichment_error`)
    rather than propagated, so a batch run continues past one bad item."""
    config = config or get_config()

    with session_scope(config) as conn:
        items = fetch_items(conn, [item_id])
        item = items.get(item_id)
        if item is None:
            logger.warning("process_item: item %s not found, skipping", item_id)
            return
        conn.execute(
            "UPDATE items SET enrichment_status = ? WHERE id = ?",
            (EnrichmentStatus.RUNNING.value, item_id),
        )

    try:
        await ollama_client.ensure_running(config)
        needs_vision = any(not mf.vision_caption for mf in item.media_files)
        if needs_vision:
            await ollama_client.ensure_model_pulled(config.models.vision_model, config)
        await ollama_client.ensure_model_pulled(config.models.embedding_model, config)

        for media_file in item.media_files:
            if media_file.media_type == "photo":
                if not media_file.vision_caption:
                    caption = await _caption_photo(config, media_file)
                    with session_scope(config) as conn:
                        _update_media_file(conn, media_file.id, vision_caption=caption)
                    media_file.vision_caption = caption
            else:  # "video"
                if not media_file.vision_caption:
                    caption = await _caption_video(config, media_file)
                    with session_scope(config) as conn:
                        _update_media_file(conn, media_file.id, vision_caption=caption)
                    media_file.vision_caption = caption
                if media_file.transcript is None:
                    transcript = await _transcribe_video(config, media_file)
                    with session_scope(config) as conn:
                        _update_media_file(conn, media_file.id, transcript=transcript)
                    media_file.transcript = transcript

        await _embed_and_upsert(config, item)

        with session_scope(config) as conn:
            _set_item_status(conn, item_id, EnrichmentStatus.DONE)

    except Exception as exc:  # noqa: BLE001 - broad on purpose: any failure marks the item failed
        logger.exception("Enrichment failed for item %s", item_id)
        with session_scope(config) as conn:
            _set_item_status(conn, item_id, EnrichmentStatus.FAILED, error=str(exc))


async def process_items(item_ids: list[int], config: Config | None = None) -> None:
    """Sequentially process a batch of items — the "worker" side of the
    job queue, scheduled as a background task by `routes_enrich.run_enrichment`."""
    config = config or get_config()
    for item_id in item_ids:
        await process_item(item_id, config)


# --- queue resolution helpers (used by routes_enrich.py) -----------------------


def resolve_target_item_ids(conn: sqlite3.Connection, item_ids: list[int] | None) -> list[int]:
    """Resolve the target item id list for a `/run` request: the explicit
    `item_ids` if given, otherwise every item currently `pending`."""
    if item_ids is not None:
        return list(item_ids)
    rows = conn.execute(
        "SELECT id FROM items WHERE enrichment_status = ?", (EnrichmentStatus.PENDING.value,)
    ).fetchall()
    return [row["id"] for row in rows]


def mark_items_pending(conn: sqlite3.Connection, item_ids: list[int]) -> None:
    """Flip the given (existing) items to `pending` immediately, so
    `/api/enrich/progress` reflects them as queued right away rather than
    waiting for the background task to actually start."""
    if not item_ids:
        return
    placeholders = ",".join("?" for _ in item_ids)
    conn.execute(
        f"UPDATE items SET enrichment_status = ? WHERE id IN ({placeholders})",
        (EnrichmentStatus.PENDING.value, *item_ids),
    )
