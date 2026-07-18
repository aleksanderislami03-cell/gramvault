"""Read-only loading of `Item` (+ nested Author/MediaFile/Tag) rows out of
the shared SQLite DB, for the exporter.

Agent A6 does not own the items/media_files/tags tables (A2/A3 do) but
needs a fully-populated `Item` graph to render notes, and
`routes_library.py` (A2/A5's query surface) is still a stub — so this
module talks to the DB directly via plain SQL, read-only, matching the
shape defined in `models/schemas.py` and `db/schema.sql`. If A2 later
lands real query helpers in routes_library.py, this can be swapped for
those without changing `exporter.py`'s call site.
"""

from __future__ import annotations

import sqlite3

from gramvault.models.schemas import Author, Item, MediaFile, Tag


def _load_author(conn: sqlite3.Connection, author_id: int | None) -> Author | None:
    if author_id is None:
        return None
    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    return Author.model_validate(dict(row)) if row is not None else None


def _load_media_files(conn: sqlite3.Connection, item_id: int) -> list[MediaFile]:
    rows = conn.execute(
        "SELECT * FROM media_files WHERE item_id = ? ORDER BY sequence_index",
        (item_id,),
    ).fetchall()
    return [MediaFile.model_validate(dict(row)) for row in rows]


def _load_tags(conn: sqlite3.Connection, item_id: int) -> list[Tag]:
    rows = conn.execute(
        """
        SELECT tags.id, tags.name, tags.kind
        FROM tags
        JOIN item_tags ON item_tags.tag_id = tags.id
        WHERE item_tags.item_id = ?
        ORDER BY tags.name
        """,
        (item_id,),
    ).fetchall()
    return [Tag.model_validate(dict(row)) for row in rows]


def load_items(conn: sqlite3.Connection, item_ids: list[int] | None = None) -> list[Item]:
    """Load `Item`s (with `author`, `media_files`, `tags` populated).

    `item_ids=None` loads the whole library; an empty list loads nothing.
    """
    if item_ids is not None and len(item_ids) == 0:
        return []

    query = "SELECT * FROM items"
    params: tuple[object, ...] = ()
    if item_ids is not None:
        placeholders = ",".join("?" for _ in item_ids)
        query += f" WHERE id IN ({placeholders})"
        params = tuple(item_ids)
    query += " ORDER BY id"

    rows = conn.execute(query, params).fetchall()

    items: list[Item] = []
    for row in rows:
        data = dict(row)
        item_id = data["id"]
        items.append(
            Item(
                id=item_id,
                external_id=data.get("external_id"),
                author=_load_author(conn, data.get("author_id")),
                media_type=data["media_type"],
                caption=data.get("caption"),
                permalink=data.get("permalink"),
                taken_at=data.get("taken_at"),
                imported_at=data.get("imported_at"),
                import_job_id=data.get("import_job_id"),
                enrichment_status=data.get("enrichment_status", "pending"),
                tags=_load_tags(conn, item_id),
                media_files=_load_media_files(conn, item_id),
            )
        )
    return items


__all__ = ["load_items"]
