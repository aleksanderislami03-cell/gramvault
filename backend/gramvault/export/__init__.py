"""Obsidian export package (Agent A6).

Turns library `Item`/`MediaFile` rows into a folder of Markdown notes
plus a Dataview-friendly index, inside a user-configured Obsidian vault.

Modules:
    markdown_builder — render a single Item as a Markdown note (frontmatter
        + body), sanitize stable cross-platform filenames.
    index_builder     — render "GramVault Index.md" (Dataview block + a
        plain-Markdown fallback table).
    repository        — load Item/MediaFile/Author/Tag rows out of the
        shared SQLite DB for export (read-only; A6 does not write to
        items/media_files/tags).
    exporter           — orchestrates the above: resolves/validates the
        vault path, writes notes + copies media idempotently keyed off
        each item's stable `gramvault_id` (== Item.id), writes the index.

See `backend/gramvault/api/routes_export.py` for the HTTP surface.
"""

from __future__ import annotations
