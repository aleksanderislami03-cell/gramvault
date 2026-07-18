"""Merges an item's per-media captions/transcript/original metadata into a
single "content document" string — the text that gets chunked + embedded
into ChromaDB, and shown back to the user as a citation snippet in chat.

Kept as a small pure-function module (no I/O) so it's trivial to unit test.
"""

from __future__ import annotations

from gramvault.models.schemas import Item


def build_content_document(item: Item) -> str:
    """Build the combined text document for one item.

    Merges (in a stable, readable order): author, original Instagram
    caption, manual/auto/hashtag tags, then each media file's vision
    caption and transcript. Blank/missing fields are omitted rather than
    rendered as empty lines.
    """
    lines: list[str] = []

    if item.author is not None and item.author.username:
        author_line = f"Author: @{item.author.username}"
        if item.author.full_name:
            author_line += f" ({item.author.full_name})"
        lines.append(author_line)

    if item.caption:
        lines.append(f"Original caption: {item.caption.strip()}")

    if item.tags:
        tag_names = ", ".join(sorted({t.name for t in item.tags}))
        if tag_names:
            lines.append(f"Tags: {tag_names}")

    for media_file in item.media_files:
        label = f"Media {media_file.sequence_index + 1}" if item.media_files else "Media"
        if media_file.vision_caption:
            lines.append(f"{label} visual description: {media_file.vision_caption.strip()}")
        if media_file.transcript:
            lines.append(f"{label} transcript: {media_file.transcript.strip()}")

    return "\n".join(lines)


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split `text` into overlapping character-window chunks of at most
    `chunk_size` characters, stepping forward by `chunk_size - chunk_overlap`
    each time. Returns `[text]` unchanged if it already fits in one chunk
    (the common case for GramVault's fairly short captions/transcripts).

    This is deliberately simple (character windows, not token-aware) —
    good enough for local embedding of short-to-medium AI-generated text.
    """
    text = text.strip()
    if not text:
        return []
    if chunk_size <= 0 or len(text) <= chunk_size:
        return [text]

    step = max(1, chunk_size - max(0, chunk_overlap))
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start += step
    return chunks
