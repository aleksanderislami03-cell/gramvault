"""Library/gallery API (Agent A2, consumed by Agent A5's frontend).

Owns: the browsable gallery grid, filtering by author/media type/tag/date/
free-text, single-item detail, and manual tag editing.

Uses plain SQL (via `gramvault.db.session.session_scope`) joining
items/authors/media_files/tags, per the project's "sqlite3, not an ORM"
convention (see `gramvault/db/session.py`).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from gramvault.api.deps import get_config_dependency
from gramvault.config import Config
from gramvault.db.session import session_scope
from gramvault.models.schemas import Author, Item, MediaFile, MediaType, Tag

router = APIRouter(prefix="/api/library", tags=["library"])

_ITEM_SELECT = """
    SELECT items.*, authors.username AS author_username, authors.full_name AS author_full_name,
           authors.profile_url AS author_profile_url, authors.avatar_path AS author_avatar_path
    FROM items
    LEFT JOIN authors ON authors.id = items.author_id
"""


class ItemListResponse(BaseModel):
    items: list[Item]
    total: int
    page: int
    page_size: int


class TagUpdateRequest(BaseModel):
    tags: list[str]


def _fetch_media_files(conn: sqlite3.Connection, item_id: int) -> list[MediaFile]:
    rows = conn.execute(
        "SELECT * FROM media_files WHERE item_id = ? ORDER BY sequence_index", (item_id,)
    ).fetchall()
    return [MediaFile.model_validate(dict(row)) for row in rows]


def _fetch_tags(conn: sqlite3.Connection, item_id: int) -> list[Tag]:
    rows = conn.execute(
        """
        SELECT tags.* FROM tags
        JOIN item_tags ON item_tags.tag_id = tags.id
        WHERE item_tags.item_id = ?
        ORDER BY tags.name
        """,
        (item_id,),
    ).fetchall()
    return [Tag.model_validate(dict(row)) for row in rows]


def _row_to_item(conn: sqlite3.Connection, row: sqlite3.Row) -> Item:
    data = dict(row)
    author = None
    if data.get("author_id") is not None:
        author = Author(
            id=data["author_id"],
            username=data["author_username"],
            full_name=data["author_full_name"],
            profile_url=data["author_profile_url"],
            avatar_path=data["author_avatar_path"],
        )
    return Item(
        id=data["id"],
        external_id=data["external_id"],
        author=author,
        media_type=data["media_type"],
        caption=data["caption"],
        permalink=data["permalink"],
        taken_at=data["taken_at"],
        imported_at=data["imported_at"],
        import_job_id=data["import_job_id"],
        enrichment_status=data["enrichment_status"],
        tags=_fetch_tags(conn, data["id"]),
        media_files=_fetch_media_files(conn, data["id"]),
    )


@router.get("/items", response_model=ItemListResponse)
async def list_items(
    author: str | None = Query(default=None, description="Filter by author username"),
    media_type: MediaType | None = Query(default=None, description="Filter by media type"),
    tag: str | None = Query(default=None, description="Filter by tag name"),
    q: str | None = Query(default=None, description="Free-text search over captions"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    config: Config = Depends(get_config_dependency),
) -> ItemListResponse:
    """Paginated, filterable gallery listing, joined with each item's
    author/tags/media_files."""
    clauses: list[str] = []
    params: list[object] = []
    joins = ""

    if author:
        clauses.append("authors.username = ?")
        params.append(author)
    if media_type:
        clauses.append("items.media_type = ?")
        params.append(media_type.value)
    if q:
        clauses.append("items.caption LIKE ?")
        params.append(f"%{q}%")
    if date_from:
        clauses.append("items.taken_at >= ?")
        params.append(date_from.isoformat())
    if date_to:
        clauses.append("items.taken_at <= ?")
        params.append(date_to.isoformat())
    if tag:
        joins = "JOIN item_tags ON item_tags.item_id = items.id JOIN tags ON tags.id = item_tags.tag_id"
        clauses.append("tags.name = ?")
        params.append(tag)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with session_scope(config) as conn:
        total_row = conn.execute(
            f"SELECT COUNT(DISTINCT items.id) AS c FROM items "
            f"LEFT JOIN authors ON authors.id = items.author_id {joins} {where_sql}",
            params,
        ).fetchone()
        total = total_row["c"] if total_row else 0

        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT DISTINCT items.* FROM items "
            f"LEFT JOIN authors ON authors.id = items.author_id {joins} {where_sql} "
            f"ORDER BY items.imported_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
        ids = [row["id"] for row in rows]
        items = []
        if ids:
            placeholders = ",".join("?" * len(ids))
            full_rows = conn.execute(
                f"{_ITEM_SELECT} WHERE items.id IN ({placeholders})", ids
            ).fetchall()
            by_id = {row["id"]: row for row in full_rows}
            items = [_row_to_item(conn, by_id[item_id]) for item_id in ids if item_id in by_id]

    return ItemListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/items/{item_id}", response_model=Item)
async def get_item(
    item_id: int,
    config: Config = Depends(get_config_dependency),
) -> Item:
    """Fetch a single item with its media files and tags."""
    with session_scope(config) as conn:
        row = conn.execute(f"{_ITEM_SELECT} WHERE items.id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
        item = _row_to_item(conn, row)
    return item


@router.get("/authors", response_model=list[Author])
async def list_authors(
    config: Config = Depends(get_config_dependency),
) -> list[Author]:
    """List all known authors (for filter dropdowns)."""
    with session_scope(config) as conn:
        rows = conn.execute("SELECT * FROM authors ORDER BY username").fetchall()
    return [Author.model_validate(dict(row)) for row in rows]


@router.get("/tags", response_model=list[Tag])
async def list_tags(
    config: Config = Depends(get_config_dependency),
) -> list[Tag]:
    """List all known tags (for filter dropdowns / tag-cloud UI)."""
    with session_scope(config) as conn:
        rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
    return [Tag.model_validate(dict(row)) for row in rows]


@router.patch("/items/{item_id}/tags", response_model=Item)
async def update_item_tags(
    item_id: int,
    body: TagUpdateRequest,
    config: Config = Depends(get_config_dependency),
) -> Item:
    """Replace an item's manually-assigned tags.

    Decision: only tags of kind='manual' are replaced by this endpoint —
    auto-generated tags (from A3's AI pipeline) and hashtag tags (parsed
    from captions) are left untouched, since a user editing "their" tags
    shouldn't accidentally wipe out AI-generated ones. Any name in
    `body.tags` that doesn't already exist as a tag is created as
    kind='manual'; if it already exists under any kind, the existing tag
    row is simply (re-)linked to this item.
    """
    with session_scope(config) as conn:
        exists = conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

        conn.execute(
            """
            DELETE FROM item_tags
            WHERE item_id = ?
              AND tag_id IN (SELECT id FROM tags WHERE kind = 'manual')
            """,
            (item_id,),
        )
        for raw_name in body.tags:
            name = raw_name.strip()
            if not name:
                continue
            conn.execute(
                "INSERT INTO tags (name, kind) VALUES (?, 'manual') "
                "ON CONFLICT(name) DO NOTHING",
                (name,),
            )
            tag_row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                (item_id, tag_row["id"]),
            )

        row = conn.execute(f"{_ITEM_SELECT} WHERE items.id = ?", (item_id,)).fetchone()
        item = _row_to_item(conn, row)
    return item
