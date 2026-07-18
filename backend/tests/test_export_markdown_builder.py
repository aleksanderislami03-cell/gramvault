"""Tests for gramvault.export.markdown_builder: frontmatter correctness,
filename sanitization/stability, and body rendering with partial A3
enrichment data (transcript/vision_caption may be None)."""

from __future__ import annotations

from datetime import datetime

import pytest

from gramvault.export.markdown_builder import (
    MediaLink,
    build_frontmatter,
    build_note_markdown,
    extract_gramvault_id,
    media_filename,
    note_filename,
    render_frontmatter,
    sanitize_filename_component,
)
from gramvault.models.schemas import Author, FileMediaType, Item, MediaFile, MediaType, Tag


def _make_item(**overrides) -> Item:
    defaults: dict = {
        "id": 42,
        "author": Author(id=1, username="jane.doe"),
        "media_type": MediaType.PHOTO,
        "caption": "A lovely sunset",
        "permalink": "https://instagram.com/p/abc123/",
        "taken_at": datetime(2024, 3, 15, 10, 30),
        "tags": [Tag(id=1, name="sunset"), Tag(id=2, name="travel")],
        "media_files": [],
    }
    defaults.update(overrides)
    return Item(**defaults)


class TestSanitizeFilenameComponent:
    def test_strips_windows_invalid_chars(self) -> None:
        result = sanitize_filename_component('a<b>c:d"e/f\\g|h?i*j')
        assert not any(ch in result for ch in '<>:"/\\|?*')

    def test_strips_control_chars(self) -> None:
        result = sanitize_filename_component("hello\x00world\x1f")
        assert "\x00" not in result
        assert "\x1f" not in result

    def test_collapses_whitespace(self) -> None:
        assert sanitize_filename_component("hello   world") == "hello-world"

    def test_strips_trailing_dots_and_spaces(self) -> None:
        result = sanitize_filename_component("filename.. ")
        assert not result.endswith(".")
        assert not result.endswith(" ")

    def test_empty_input_falls_back_to_untitled(self) -> None:
        assert sanitize_filename_component("") == "untitled"

    def test_all_invalid_chars_falls_back_to_untitled(self) -> None:
        assert sanitize_filename_component('<>:"/\\|?*') == "untitled"

    def test_windows_reserved_name_is_escaped(self) -> None:
        result = sanitize_filename_component("CON")
        assert result.upper() != "CON"

    def test_reserved_name_check_is_case_insensitive(self) -> None:
        result = sanitize_filename_component("con")
        assert result.upper() != "CON"

    def test_respects_max_length(self) -> None:
        result = sanitize_filename_component("a" * 200, max_length=10)
        assert len(result) <= 10


class TestNoteFilename:
    def test_is_stable_across_calls(self) -> None:
        item = _make_item()
        assert note_filename(item) == note_filename(item)

    def test_embeds_item_id(self) -> None:
        item = _make_item(id=999)
        assert "999" in note_filename(item)

    def test_raises_without_id(self) -> None:
        item = _make_item(id=None)
        with pytest.raises(ValueError):
            note_filename(item)

    def test_unknown_author_falls_back(self) -> None:
        item = _make_item(author=None)
        filename = note_filename(item)
        assert "unknown" in filename

    def test_sanitizes_unsafe_author_username(self) -> None:
        item = _make_item(author=Author(id=1, username='weird/name:here"'))
        filename = note_filename(item)
        assert not any(ch in filename for ch in '/:"')

    def test_no_date_falls_back(self) -> None:
        item = _make_item(taken_at=None, imported_at=None)
        assert "nodate" in note_filename(item)


class TestMediaFilename:
    def test_preserves_extension(self) -> None:
        item = _make_item()
        media_file = MediaFile(
            id=1, item_id=42, file_path="/some/path/photo.jpg", media_type=FileMediaType.PHOTO
        )
        assert media_filename(item, media_file).endswith(".jpg")

    def test_stable_across_calls(self) -> None:
        item = _make_item()
        media_file = MediaFile(
            id=1, item_id=42, file_path="/some/path/photo.jpg", media_type=FileMediaType.PHOTO
        )
        assert media_filename(item, media_file) == media_filename(item, media_file)

    def test_namespaced_by_item_and_sequence(self) -> None:
        item = _make_item(id=7)
        mf0 = MediaFile(id=1, item_id=7, file_path="a.jpg", media_type=FileMediaType.PHOTO, sequence_index=0)
        mf1 = MediaFile(id=2, item_id=7, file_path="b.jpg", media_type=FileMediaType.PHOTO, sequence_index=1)
        assert media_filename(item, mf0) != media_filename(item, mf1)


class TestFrontmatter:
    def test_contains_required_fields(self) -> None:
        item = _make_item()
        fm = build_frontmatter(item)
        assert fm["author"] == "jane.doe"
        assert fm["date"] == "2024-03-15T10:30:00"
        assert fm["type"] == "photo"
        assert fm["tags"] == ["sunset", "travel"]
        assert fm["source_url"] == "https://instagram.com/p/abc123/"
        assert fm["gramvault_id"] == 42

    def test_render_roundtrips_gramvault_id(self) -> None:
        item = _make_item(id=123)
        fm = build_frontmatter(item)
        rendered = render_frontmatter(fm)
        assert rendered.startswith("---\n")
        note = rendered + "\nbody text\n"
        assert extract_gramvault_id(note) == 123

    def test_extract_gramvault_id_missing_frontmatter(self) -> None:
        assert extract_gramvault_id("just some text, no frontmatter") is None

    def test_extract_gramvault_id_malformed_yaml(self) -> None:
        assert extract_gramvault_id("---\n: this is not: valid: yaml: [\n---\nbody") is None

    def test_no_author_yields_none(self) -> None:
        item = _make_item(author=None)
        assert build_frontmatter(item)["author"] is None

    def test_falls_back_to_imported_at(self) -> None:
        item = _make_item(taken_at=None, imported_at=datetime(2023, 1, 1))
        assert build_frontmatter(item)["date"] == "2023-01-01T00:00:00"


class TestBuildNoteMarkdown:
    def test_includes_caption(self) -> None:
        item = _make_item(caption="Hello world")
        assert "Hello world" in build_note_markdown(item)

    def test_missing_caption_uses_placeholder(self) -> None:
        item = _make_item(caption=None)
        assert "No caption" in build_note_markdown(item)

    def test_transcript_and_vision_caption_optional(self) -> None:
        # A3 may not have finished enrichment — both fields None should
        # not raise and should not fabricate sections.
        item = _make_item(
            media_files=[
                MediaFile(
                    id=1,
                    item_id=42,
                    file_path="clip.mp4",
                    media_type=FileMediaType.VIDEO,
                    transcript=None,
                    vision_caption=None,
                )
            ]
        )
        note = build_note_markdown(item)
        assert "## Transcript" not in note
        assert "## AI Description" not in note

    def test_transcript_and_vision_caption_included_when_present(self) -> None:
        item = _make_item(
            media_files=[
                MediaFile(
                    id=1,
                    item_id=42,
                    file_path="clip.mp4",
                    media_type=FileMediaType.VIDEO,
                    transcript="hello from the transcript",
                    vision_caption="a person waving",
                )
            ]
        )
        note = build_note_markdown(item)
        assert "## Transcript" in note
        assert "hello from the transcript" in note
        assert "## AI Description" in note
        assert "a person waving" in note

    def test_embed_media_link_rendered_as_wikilink(self) -> None:
        item = _make_item(
            media_files=[
                MediaFile(id=1, item_id=42, file_path="a.jpg", media_type=FileMediaType.PHOTO)
            ]
        )
        link = MediaLink(item.media_files[0], "media/42_0.jpg", embed=True)
        note = build_note_markdown(item, [link])
        assert "![[media/42_0.jpg]]" in note

    def test_link_only_media_rendered_as_plain_link(self) -> None:
        item = _make_item(
            media_files=[
                MediaFile(id=1, item_id=42, file_path="/orig/a.jpg", media_type=FileMediaType.PHOTO)
            ]
        )
        link = MediaLink(item.media_files[0], "/orig/a.jpg", embed=False)
        note = build_note_markdown(item, [link])
        assert "![[" not in note
        assert "(/orig/a.jpg)" in note

    def test_frontmatter_gramvault_id_is_parseable_back_out(self) -> None:
        item = _make_item(id=555)
        note = build_note_markdown(item)
        assert extract_gramvault_id(note) == 555
