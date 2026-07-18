"""Hybrid retrieval over the GramVault library: vector search (ChromaDB,
via `gramvault.ai.embedding_store`) combined with a keyword/full-text pass
over SQLite.

FTS5 note: `backend/gramvault/db/schema.sql` does not define any FTS5
virtual tables (no `CREATE VIRTUAL TABLE ... USING fts5`), so this module
falls back to a `LIKE`-based keyword search (see `keyword_search()` below).
This is an acceptable v1 per the task brief — if a future revision adds an
FTS5 table (e.g. `items_fts`), swap `keyword_search()`'s query for an FTS
MATCH query and this module's public interface (`hybrid_search`,
`RetrievalResult`) shouldn't need to change.

Merge/rerank strategy (intentionally simple, not "academic"):
    1. Run vector search and keyword search independently.
    2. Vector results keep their similarity-based ordering.
    3. Any item present in *both* result sets gets a score boost (it's
       corroborated by two independent signals) and is pulled to the top.
    4. Keyword-only results are appended after, sorted by their own score.
    5. Dedupe by item_id (an item may have multiple embedded chunks; keep
       the highest-scoring chunk's snippet).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from gramvault.ai import embedding_store, ollama_client
from gramvault.config import Config, get_config
from gramvault.models.schemas import Author, Item, MediaFile, Tag

# Flat bonus applied when an item appears in both the vector and keyword
# result sets (see module docstring, step 3).
_BOTH_SOURCES_BOOST = 0.25

# Per-field weights for the LIKE-based keyword score (module docstring
# step 2's "not academic" keyword scoring — a field-presence heuristic).
_FIELD_WEIGHTS = {
    "caption": 0.35,
    "tag_names": 0.25,
    "vision_captions": 0.2,
    "transcripts": 0.2,
    "username": 0.15,
    "full_name": 0.1,
}


@dataclass
class RetrievalResult:
    item_id: int
    score: float
    media_file_id: int | None = None
    snippet: str | None = None
    sources: set[str] = field(default_factory=set)


async def embed_query(query: str, config: Config | None = None) -> list[float]:
    """Embed the user's free-text query with the configured embedding
    model. Propagates `OllamaNotRunningError`/`ModelNotPulledError`."""
    config = config or get_config()
    return await ollama_client.embed(query, model=config.models.embedding_model, config=config)


def vector_search(
    query_embedding: list[float], top_k: int = 10, config: Config | None = None
) -> list[RetrievalResult]:
    """Nearest-neighbor search over ChromaDB, already ordered nearest-first."""
    raw_results = embedding_store.query(query_embedding, top_k=top_k, config=config)
    return [
        RetrievalResult(
            item_id=r.item_id,
            score=r.score,
            media_file_id=r.media_file_id,
            snippet=r.snippet,
            sources={"vector"},
        )
        for r in raw_results
    ]


def keyword_search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[RetrievalResult]:
    """LIKE-based keyword fallback over item captions/authors/tags/AI text.

    See module docstring — no FTS5 table exists in schema.sql yet, so this
    is a pragmatic v1: one aggregated row per item, scored by which
    field(s) the query substring shows up in.
    """
    query = query.strip()
    if not query:
        return []
    like_pattern = f"%{query}%"

    rows = conn.execute(
        """
        SELECT
            i.id AS item_id,
            i.caption AS caption,
            a.username AS username,
            a.full_name AS full_name,
            GROUP_CONCAT(DISTINCT mf.transcript) AS transcripts,
            GROUP_CONCAT(DISTINCT mf.vision_caption) AS vision_captions,
            GROUP_CONCAT(DISTINCT t.name) AS tag_names
        FROM items i
        LEFT JOIN authors a ON a.id = i.author_id
        LEFT JOIN media_files mf ON mf.item_id = i.id
        LEFT JOIN item_tags it ON it.item_id = i.id
        LEFT JOIN tags t ON t.id = it.tag_id
        GROUP BY i.id
        HAVING
            (i.caption LIKE :pat)
            OR (a.username LIKE :pat)
            OR (a.full_name LIKE :pat)
            OR (transcripts LIKE :pat)
            OR (vision_captions LIKE :pat)
            OR (tag_names LIKE :pat)
        LIMIT :limit
        """,
        {"pat": like_pattern, "limit": limit},
    ).fetchall()

    query_lower = query.lower()
    results: list[RetrievalResult] = []
    for row in rows:
        score = 0.0
        snippet: str | None = None
        for field_name, weight in _FIELD_WEIGHTS.items():
            value = row[field_name]
            if value and query_lower in value.lower():
                score += weight
                if snippet is None:
                    snippet = value
        results.append(
            RetrievalResult(
                item_id=row["item_id"],
                score=min(score, 1.0),
                snippet=snippet,
                sources={"keyword"},
            )
        )
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def merge_results(
    vector_results: list[RetrievalResult],
    keyword_results: list[RetrievalResult],
    top_k: int = 8,
) -> list[RetrievalResult]:
    """Union + dedupe + rerank per the module docstring's merge strategy."""
    by_item: dict[int, RetrievalResult] = {}

    for r in vector_results:
        existing = by_item.get(r.item_id)
        if existing is None or r.score > existing.score:
            by_item[r.item_id] = r
        else:
            existing.sources |= r.sources

    for r in keyword_results:
        existing = by_item.get(r.item_id)
        if existing is None:
            by_item[r.item_id] = r
        else:
            existing.sources |= r.sources
            if existing.snippet is None:
                existing.snippet = r.snippet

    for result in by_item.values():
        if len(result.sources) > 1:
            result.score = min(1.0, result.score + _BOTH_SOURCES_BOOST)

    merged = sorted(
        by_item.values(),
        key=lambda r: (len(r.sources) > 1, r.score),
        reverse=True,
    )
    return merged[:top_k]


async def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 8,
    config: Config | None = None,
) -> list[RetrievalResult]:
    """Embed `query`, run vector + keyword search, and return the merged,
    reranked, deduped top `top_k` results."""
    config = config or get_config()
    query_embedding = await embed_query(query, config=config)
    vector_results = vector_search(query_embedding, top_k=top_k * 2, config=config)
    keyword_results = keyword_search(conn, query, limit=top_k * 2)
    return merge_results(vector_results, keyword_results, top_k=top_k)


def fetch_items(conn: sqlite3.Connection, item_ids: list[int]) -> dict[int, Item]:
    """Resolve a list of item ids into fully-populated `Item` objects
    (author, media files, tags) in a handful of queries. Missing ids are
    simply absent from the returned dict."""
    if not item_ids:
        return {}

    placeholders = ",".join("?" for _ in item_ids)
    item_rows = conn.execute(
        f"""
        SELECT i.*, a.id AS author_id_, a.username AS author_username,
               a.full_name AS author_full_name, a.profile_url AS author_profile_url,
               a.avatar_path AS author_avatar_path, a.created_at AS author_created_at
        FROM items i
        LEFT JOIN authors a ON a.id = i.author_id
        WHERE i.id IN ({placeholders})
        """,
        item_ids,
    ).fetchall()

    items: dict[int, Item] = {}
    for row in item_rows:
        author = None
        if row["author_id_"] is not None:
            author = Author(
                id=row["author_id_"],
                username=row["author_username"],
                full_name=row["author_full_name"],
                profile_url=row["author_profile_url"],
                avatar_path=row["author_avatar_path"],
                created_at=row["author_created_at"],
            )
        items[row["id"]] = Item(
            id=row["id"],
            external_id=row["external_id"],
            author=author,
            media_type=row["media_type"],
            caption=row["caption"],
            permalink=row["permalink"],
            taken_at=row["taken_at"],
            imported_at=row["imported_at"],
            import_job_id=row["import_job_id"],
            enrichment_status=row["enrichment_status"],
            tags=[],
            media_files=[],
        )

    media_rows = conn.execute(
        f"SELECT * FROM media_files WHERE item_id IN ({placeholders}) ORDER BY sequence_index",
        item_ids,
    ).fetchall()
    for row in media_rows:
        item = items.get(row["item_id"])
        if item is not None:
            item.media_files.append(MediaFile.model_validate(dict(row)))

    tag_rows = conn.execute(
        f"""
        SELECT it.item_id AS item_id, t.id AS id, t.name AS name, t.kind AS kind
        FROM item_tags it
        JOIN tags t ON t.id = it.tag_id
        WHERE it.item_id IN ({placeholders})
        """,
        item_ids,
    ).fetchall()
    for row in tag_rows:
        item = items.get(row["item_id"])
        if item is not None:
            item.tags.append(Tag(id=row["id"], name=row["name"], kind=row["kind"]))

    return items
