"""Render a single `Item` as an Obsidian-flavored Markdown note.

Design:
  - Frontmatter carries `gramvault_id` (== `Item.id`) — this is the stable
    idempotency key. `exporter.py` also embeds it in the filename so a
    re-export overwrites the same file rather than creating
    `Item (1).md`-style duplicates, and scans existing notes' frontmatter
    for stragglers (e.g. if the author/date changed since the last export)
    so those get cleaned up too instead of leaking a duplicate note.
  - Body includes the caption, any AI-generated vision captions /
    transcripts left by Agent A3 (best-effort: fields may be `None` if
    enrichment hasn't run yet — that's not an error here), and one media
    reference per `MediaFile`.
  - Filenames are sanitized to be safe on Windows *and* macOS/Linux at
    once (Windows is the strictest: reserved chars `<>:"/\\|?*`, no
    trailing dot/space, reserved device names like `CON`/`COM1`).
"""

from __future__ import annotations

import re
from typing import NamedTuple

import yaml

from gramvault.models.schemas import Item, MediaFile

# Characters invalid in filenames on Windows (the strictest of the three
# platforms we care about); also covers control characters.
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RUN = re.compile(r"\s+")

# Windows reserved device names (case-insensitive), with or without an
# extension — writing "CON.md" fails on Windows.
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

INDEX_NOTE_FILENAME = "GramVault Index.md"


def sanitize_filename_component(text: str, *, max_length: int = 60) -> str:
    """Sanitize `text` into a safe filename component for Windows, macOS,
    and Linux simultaneously. Never raises; always returns a non-empty
    string ("untitled" as the last-resort fallback)."""
    if not text:
        return "untitled"

    cleaned = _INVALID_FILENAME_CHARS.sub("-", text)
    cleaned = _WHITESPACE_RUN.sub("-", cleaned.strip())
    # Windows disallows trailing dots/spaces on path components.
    cleaned = cleaned.strip(". ")
    cleaned = cleaned.strip("-")

    if not cleaned:
        return "untitled"

    cleaned = cleaned[:max_length].strip("-")
    if not cleaned:
        return "untitled"

    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"

    return cleaned


def note_filename(item: Item) -> str:
    """Stable, cross-platform-safe filename for `item`'s note.

    Embeds `item.id` (the `gramvault_id`) so re-exporting the same item
    always resolves to the same path — the idempotency key for exports.
    """
    if item.id is None:
        raise ValueError("Item.id is required to compute a stable export filename")

    author_part = sanitize_filename_component(
        item.author.username if item.author is not None else "unknown", max_length=40
    )
    date_source = item.taken_at or item.imported_at
    date_part = date_source.strftime("%Y-%m-%d") if date_source else "nodate"

    return f"{date_part}_{author_part}_{item.id}.md"


def media_filename(item: Item, media_file: MediaFile) -> str:
    """Stable filename for a copied-into-vault media file, namespaced by
    item id + sequence index so carousels don't collide and re-exports
    overwrite the same copy."""
    original_suffix = ""
    if "." in media_file.file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]:
        original_suffix = "." + media_file.file_path.rsplit(".", 1)[-1]
    original_suffix = sanitize_filename_component(original_suffix, max_length=10) if original_suffix else ""
    if original_suffix and not original_suffix.startswith("."):
        original_suffix = f".{original_suffix}"
    return f"{item.id}_{media_file.sequence_index}{original_suffix}"


class MediaLink(NamedTuple):
    """How a single MediaFile should be referenced from the note body."""

    media_file: MediaFile
    # Obsidian-relative path (embed) or a plain link target (link mode /
    # copy failed) — interpretation depends on `embed`.
    target: str
    embed: bool


def _media_type_value(item: Item) -> str:
    media_type = item.media_type
    return media_type.value if hasattr(media_type, "value") else str(media_type)


def build_frontmatter(item: Item) -> dict[str, object]:
    """Build the YAML-frontmatter dict for `item`. `gramvault_id` is the
    stable re-export lookup key described in the module docstring."""
    date_value = item.taken_at or item.imported_at
    return {
        "author": item.author.username if item.author is not None else None,
        "date": date_value.isoformat() if date_value else None,
        "type": _media_type_value(item),
        "tags": [tag.name for tag in item.tags],
        "source_url": item.permalink,
        "gramvault_id": item.id,
    }


def render_frontmatter(frontmatter: dict[str, object]) -> str:
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{yaml_text}\n---\n"


def extract_gramvault_id(note_text: str) -> int | None:
    """Best-effort parse of the `gramvault_id` frontmatter field out of an
    existing note's text. Returns None if there's no parseable frontmatter
    or no `gramvault_id` key — never raises."""
    if not note_text.startswith("---"):
        return None
    end = note_text.find("\n---", 3)
    if end == -1:
        return None
    try:
        data = yaml.safe_load(note_text[3:end])
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    gramvault_id = data.get("gramvault_id")
    return gramvault_id if isinstance(gramvault_id, int) else None


def _build_body(item: Item, media_links: list[MediaLink]) -> str:
    sections: list[str] = []

    sections.append(item.caption.strip() if item.caption else "*No caption.*")

    vision_captions = [mf.vision_caption for mf in item.media_files if mf.vision_caption]
    if vision_captions:
        bullet_list = "\n".join(f"- {caption}" for caption in vision_captions)
        sections.append(f"## AI Description\n\n{bullet_list}")

    transcripts = [mf.transcript for mf in item.media_files if mf.transcript]
    if transcripts:
        transcript_text = "\n\n".join(transcripts)
        sections.append(f"## Transcript\n\n{transcript_text}")

    if media_links:
        media_lines = []
        for link in media_links:
            if link.embed:
                media_lines.append(f"![[{link.target}]]")
            else:
                media_lines.append(f"[{link.media_file.media_type.value}]({link.target})")
        sections.append("## Media\n\n" + "\n\n".join(media_lines))

    if item.permalink:
        sections.append(f"[Original post]({item.permalink})")

    return "\n\n".join(sections) + "\n"


def build_note_markdown(item: Item, media_links: list[MediaLink] | None = None) -> str:
    """Full Markdown note text (frontmatter + body) for `item`."""
    media_links = media_links or []
    frontmatter = render_frontmatter(build_frontmatter(item))
    body = _build_body(item, media_links)
    return f"{frontmatter}\n{body}"


__all__ = [
    "INDEX_NOTE_FILENAME",
    "MediaLink",
    "build_frontmatter",
    "build_note_markdown",
    "extract_gramvault_id",
    "media_filename",
    "note_filename",
    "render_frontmatter",
    "sanitize_filename_component",
]
