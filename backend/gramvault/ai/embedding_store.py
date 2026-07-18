"""ChromaDB wrapper for item/media-file embeddings.

SCAFFOLD NOTICE (from Agent A4, chat/search):
    This file did not exist yet when A4 started building `gramvault.chat.*`
    (retrieval needs `query()`, and A3's enrichment pipeline is expected to
    call `upsert_item()` when it chunks+embeds captions/transcripts). A3:
    please review and adapt to whatever chunking/collection layout you
    actually want — just keep `upsert_item()` / `query()` signatures (or
    coordinate changes with A4) since `gramvault.chat.retrieval` calls
    `query()` directly.

Design:
    - A single persistent Chroma collection ("gramvault_items") at
      `config.resolved_chroma_dir`, one vector per (item, chunk). Each
      point's metadata carries `item_id` (int) and optionally
      `media_file_id` (int) plus a small text `snippet` for display, so
      results can be resolved back to SQLite rows without a second lookup
      for the common case.
    - IDs are opaque strings (`f"{item_id}:{chunk_index}"` by default) —
      only the metadata's `item_id`/`media_file_id` fields are semantically
      meaningful to callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import chromadb

from gramvault.config import Config, get_config

COLLECTION_NAME = "gramvault_items"


@dataclass
class QueryResult:
    item_id: int
    media_file_id: int | None
    score: float
    snippet: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def _client(config: Config) -> chromadb.ClientAPI:
    config.resolved_chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(config.resolved_chroma_dir))


def get_collection(config: Config | None = None):
    """Return the (created-if-missing) shared Chroma collection."""
    config = config or get_config()
    client = _client(config)
    return client.get_or_create_collection(COLLECTION_NAME)


def upsert_item(
    item_id: int,
    embedding: list[float],
    document: str,
    media_file_id: int | None = None,
    chunk_index: int = 0,
    extra_metadata: dict[str, Any] | None = None,
    config: Config | None = None,
) -> None:
    """Upsert a single embedded chunk for `item_id`."""
    collection = get_collection(config)
    point_id = f"{item_id}:{media_file_id or 0}:{chunk_index}"
    metadata: dict[str, Any] = {"item_id": item_id}
    if media_file_id is not None:
        metadata["media_file_id"] = media_file_id
    if extra_metadata:
        metadata.update(extra_metadata)
    collection.upsert(
        ids=[point_id],
        embeddings=[embedding],
        documents=[document],
        metadatas=[metadata],
    )


def query(embedding: list[float], top_k: int = 10, config: Config | None = None) -> list[QueryResult]:
    """Nearest-neighbor search. Returns results ordered nearest-first, with
    `score` as a similarity in roughly [0, 1] (1 - normalized distance;
    clamped, since Chroma's raw distance metric can exceed 1)."""
    collection = get_collection(config)
    if collection.count() == 0:
        return []
    n_results = min(top_k, collection.count())
    raw = collection.query(query_embeddings=[embedding], n_results=n_results)

    results: list[QueryResult] = []
    ids = raw.get("ids", [[]])[0]
    distances = raw.get("distances", [[]])[0]
    documents = raw.get("documents", [[]])[0]
    metadatas = raw.get("metadatas", [[]])[0]
    for i in range(len(ids)):
        metadata = metadatas[i] or {}
        distance = distances[i] if i < len(distances) else 1.0
        score = max(0.0, 1.0 - distance)
        results.append(
            QueryResult(
                item_id=int(metadata["item_id"]),
                media_file_id=metadata.get("media_file_id"),
                score=score,
                snippet=documents[i] if i < len(documents) else None,
                metadata=metadata,
            )
        )
    return results
