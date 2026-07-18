"""Tests for gramvault.export.index_builder: Dataview block + plain
Markdown fallback table generation."""

from __future__ import annotations

from gramvault.export.index_builder import IndexEntry, build_index_markdown


def test_includes_dataview_block() -> None:
    markdown = build_index_markdown([], subfolder_name="GramVault")
    assert "```dataview" in markdown
    assert 'FROM "GramVault"' in markdown
    assert "TABLE author, type, date, tags" in markdown


def test_includes_plain_markdown_fallback_table() -> None:
    entries = [
        IndexEntry(
            item_id=1,
            note_filename="2024-01-01_jane_1.md",
            author="jane",
            media_type="photo",
            date="2024-01-01T00:00:00",
            tags=["a", "b"],
        )
    ]
    markdown = build_index_markdown(entries, subfolder_name="GramVault")
    assert "| Author | Type | Date | Tags | Note |" in markdown
    assert "jane" in markdown
    assert "[[2024-01-01_jane_1.md]]" in markdown
    assert "a, b" in markdown


def test_empty_entries_still_produces_valid_document() -> None:
    markdown = build_index_markdown([], subfolder_name="GramVault")
    assert "0 item(s) exported." in markdown


def test_sorted_by_date_descending() -> None:
    older = IndexEntry(item_id=1, note_filename="old.md", author="a", media_type="photo", date="2023-01-01")
    newer = IndexEntry(item_id=2, note_filename="new.md", author="b", media_type="photo", date="2024-01-01")
    markdown = build_index_markdown([older, newer], subfolder_name="GramVault")
    assert markdown.index("new.md") < markdown.index("old.md")


def test_table_cell_escaping_for_pipe_characters() -> None:
    entry = IndexEntry(
        item_id=1, note_filename="x.md", author="weird|author", media_type="photo", date="2024-01-01"
    )
    markdown = build_index_markdown([entry], subfolder_name="GramVault")
    assert "weird\\|author" in markdown


def test_documents_dataview_plugin_requirement() -> None:
    # Comment in the generated file should tell readers without the
    # Dataview plugin installed that the fallback table exists.
    markdown = build_index_markdown([], subfolder_name="GramVault")
    assert "Dataview" in markdown
    assert "plain Markdown table" in markdown
