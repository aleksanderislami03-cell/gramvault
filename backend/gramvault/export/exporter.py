"""Orchestrates a full export run: resolve/validate the vault path, write
one Markdown note per Item (idempotently), copy/link media, write the
index note.

Idempotency strategy (the important bit):
  - Every note's filename is deterministic from `note_filename()`, which
    embeds the item's stable `gramvault_id` (`Item.id`). Re-running an
    export for the same item always resolves to the same path, so writing
    the file again just overwrites it in place -- no `Item (1).md`
    duplicates, no need to diff old vs. new content.
  - As a belt-and-suspenders cleanup, before writing we also scan the
    target folder's existing notes' frontmatter for a `gramvault_id` that
    matches an item we're about to (re-)export. If a stale note is found
    at a *different* path than the freshly computed filename (e.g. the
    author's username or the post's date changed between exports, which
    would otherwise change the deterministic filename), we remove the
    stale file so it doesn't linger as an orphaned duplicate.

Vault path handling:
  - `config.paths.obsidian_vault_dir` unset -> `VaultNotConfiguredError`
    (nothing to export to).
  - Configured but the base directory doesn't exist on disk ->
    `VaultPathNotFoundError` (we refuse to silently create an arbitrary
    folder tree outside a vault the user actually pointed us at).
  - The target subfolder *inside* the vault (default: `GramVault/`) is
    created automatically if missing -- that part is expected to not
    exist yet on a first export.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from gramvault.config import Config
from gramvault.export.index_builder import IndexEntry, build_index_markdown
from gramvault.export.markdown_builder import (
    INDEX_NOTE_FILENAME,
    MediaLink,
    build_note_markdown,
    extract_gramvault_id,
    media_filename,
    note_filename,
)
from gramvault.models.schemas import Item

MEDIA_SUBDIR_NAME = "media"


class VaultNotConfiguredError(Exception):
    """`config.paths.obsidian_vault_dir` is unset."""


class VaultPathNotFoundError(Exception):
    """The configured vault base directory doesn't exist / isn't a directory."""


@dataclass
class SkippedItem:
    item_id: int | None
    reason: str


@dataclass
class ExportResult:
    target_dir: Path
    notes_written: int = 0
    notes_updated: int = 0
    media_files_copied: int = 0
    skipped: list[SkippedItem] = field(default_factory=list)
    index_path: Path | None = None


def resolve_export_target(config: Config, vault_subfolder: str | None) -> Path:
    """Validate the configured vault path and return the (created) target
    subfolder within it. Raises `VaultNotConfiguredError` /
    `VaultPathNotFoundError` per the module docstring."""
    vault_dir = config.resolved_obsidian_vault_dir
    if vault_dir is None:
        raise VaultNotConfiguredError(
            "No Obsidian vault configured (set paths.obsidian_vault_dir in config.yaml)"
        )
    if not vault_dir.is_dir():
        raise VaultPathNotFoundError(f"Configured Obsidian vault folder does not exist: {vault_dir}")

    subfolder = vault_subfolder or config.export.default_vault_subfolder
    target_dir = vault_dir / subfolder
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _scan_existing_notes_by_gramvault_id(target_dir: Path) -> dict[int, Path]:
    """Best-effort map of gramvault_id -> existing note path, built by
    reading frontmatter out of every `.md` file already in `target_dir`
    (except the index). Used to clean up stale duplicates -- see the
    idempotency note in the module docstring."""
    mapping: dict[int, Path] = {}
    if not target_dir.is_dir():
        return mapping
    for path in target_dir.glob("*.md"):
        if path.name == INDEX_NOTE_FILENAME:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        gramvault_id = extract_gramvault_id(text)
        if gramvault_id is not None:
            mapping[gramvault_id] = path
    return mapping


def _copy_or_link_media(
    item: Item, media_dir: Path, media_mode: str, result: ExportResult
) -> list[MediaLink]:
    media_links: list[MediaLink] = []
    for media_file in item.media_files:
        if media_mode == "copy":
            src_path = Path(media_file.file_path)
            if src_path.is_file():
                media_dir.mkdir(parents=True, exist_ok=True)
                dest_name = media_filename(item, media_file)
                dest_path = media_dir / dest_name
                try:
                    shutil.copy2(src_path, dest_path)
                    result.media_files_copied += 1
                    media_links.append(
                        MediaLink(media_file, f"{MEDIA_SUBDIR_NAME}/{dest_name}", embed=True)
                    )
                    continue
                except OSError:
                    pass  # fall through to a plain link to the original path
            media_links.append(MediaLink(media_file, media_file.file_path, embed=False))
        else:
            media_links.append(MediaLink(media_file, media_file.file_path, embed=False))
    return media_links


def export_items(
    config: Config, items: list[Item], vault_subfolder: str | None = None
) -> ExportResult:
    """Export `items` into the configured Obsidian vault. Idempotent --
    see module docstring."""
    target_dir = resolve_export_target(config, vault_subfolder)
    media_dir = target_dir / MEDIA_SUBDIR_NAME
    media_mode = config.export.media_mode

    result = ExportResult(target_dir=target_dir)
    existing_notes_by_id = _scan_existing_notes_by_gramvault_id(target_dir)
    index_entries: list[IndexEntry] = []

    for item in items:
        if item.id is None:
            result.skipped.append(
                SkippedItem(item_id=None, reason="item has no id (not yet persisted to the DB)")
            )
            continue

        filename = note_filename(item)
        note_path = target_dir / filename

        # Clean up a stale note for this same gramvault_id living under a
        # different (now outdated) filename.
        stale_path = existing_notes_by_id.get(item.id)
        if stale_path is not None and stale_path != note_path:
            stale_path.unlink(missing_ok=True)

        existed_before = note_path.exists()
        media_links = _copy_or_link_media(item, media_dir, media_mode, result)
        note_text = build_note_markdown(item, media_links)
        note_path.write_text(note_text, encoding="utf-8")

        if existed_before:
            result.notes_updated += 1
        else:
            result.notes_written += 1

        date_source = item.taken_at or item.imported_at
        index_entries.append(
            IndexEntry(
                item_id=item.id,
                note_filename=filename,
                author=item.author.username if item.author is not None else "unknown",
                media_type=item.media_type.value if hasattr(item.media_type, "value") else str(item.media_type),
                date=date_source.isoformat() if date_source else "",
                tags=[tag.name for tag in item.tags],
            )
        )

    index_markdown = build_index_markdown(index_entries, subfolder_name=target_dir.name)
    index_path = target_dir / INDEX_NOTE_FILENAME
    index_path.write_text(index_markdown, encoding="utf-8")
    result.index_path = index_path

    return result


__all__ = [
    "MEDIA_SUBDIR_NAME",
    "ExportResult",
    "SkippedItem",
    "VaultNotConfiguredError",
    "VaultPathNotFoundError",
    "export_items",
    "resolve_export_target",
]
