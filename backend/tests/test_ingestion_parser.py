"""Tests for gramvault.ingestion.parser: locating + parsing the
saved-posts/own-posts JSON inside a (fake, in-memory) Instagram export
ZIP, and the friendly-failure paths (wrong format, HTML export, garbage
ZIP).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from gramvault.ingestion.parser import ExportFormatError, MediaType, parse_export


def _write_zip(tmp_path: Path, name: str, members: dict[str, bytes | str]) -> Path:
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        for member_name, content in members.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(member_name, data)
    return zip_path


def _saved_posts_json(entries: list[dict]) -> str:
    return json.dumps({"saved_saved_media": entries})


def test_parse_export_normal_case(tmp_path: Path) -> None:
    entries = [
        {
            "title": "chef_alice",
            "string_list_data": [
                {"href": "https://www.instagram.com/p/ABC123abc/", "timestamp": 1700000000}
            ],
        },
        {
            "title": "traveler_bob",
            "string_list_data": [
                {"href": "https://www.instagram.com/reel/XYZ789xyz/", "timestamp": 1700100000}
            ],
        },
    ]
    zip_path = _write_zip(
        tmp_path,
        "export.zip",
        {"your_instagram_activity/saved/saved_posts.json": _saved_posts_json(entries)},
    )

    parsed = parse_export(zip_path)

    assert len(parsed.saved_items) == 2
    first = parsed.saved_items[0]
    assert first.author_username == "chef_alice"
    assert first.external_id == "ABC123abc"
    assert first.instagram_url == "https://www.instagram.com/p/ABC123abc/"
    assert first.saved_at is not None
    assert first.media_type_guess == MediaType.PHOTO

    second = parsed.saved_items[1]
    assert second.media_type_guess == MediaType.REEL
    assert second.external_id == "XYZ789xyz"


def test_parse_export_reorganized_folder_layout(tmp_path: Path) -> None:
    """Instagram has moved saved_posts.json around across export versions
    -- the parser should find it via a broad glob rather than one exact
    hardcoded path."""
    entries = [
        {
            "title": "someone",
            "string_list_data": [
                {"href": "https://www.instagram.com/p/DEF456def/", "timestamp": 1700000000}
            ],
        }
    ]
    zip_path = _write_zip(
        tmp_path,
        "export.zip",
        {"connections/saved/saved_posts.json": _saved_posts_json(entries)},
    )

    parsed = parse_export(zip_path)

    assert len(parsed.saved_items) == 1
    assert parsed.saved_items[0].external_id == "DEF456def"


def test_parse_export_missing_shortcode_falls_back_to_synthetic_id(tmp_path: Path) -> None:
    entries = [
        {
            "title": "someone",
            "string_list_data": [{"href": "https://www.instagram.com/weird/url/", "timestamp": None}],
        }
    ]
    zip_path = _write_zip(
        tmp_path,
        "export.zip",
        {"your_instagram_activity/saved/saved_posts.json": _saved_posts_json(entries)},
    )

    parsed = parse_export(zip_path)

    assert len(parsed.saved_items) == 1
    assert parsed.saved_items[0].external_id is not None
    assert parsed.saved_items[0].external_id.startswith("url:")
    assert parsed.saved_items[0].saved_at is None


def test_parse_export_no_recognizable_content_raises_friendly_error(tmp_path: Path) -> None:
    zip_path = _write_zip(
        tmp_path,
        "export.zip",
        {"some_other_folder/unrelated.json": json.dumps({"foo": "bar"})},
    )

    with pytest.raises(ExportFormatError) as exc_info:
        parse_export(zip_path)

    message = str(exc_info.value)
    assert "saved-posts" in message.lower() or "posts" in message.lower()
    assert "unrelated.json" in message  # lists what was actually found


def test_parse_export_html_format_raises_reexport_error(tmp_path: Path) -> None:
    zip_path = _write_zip(
        tmp_path,
        "export.zip",
        {"your_instagram_activity/saved/saved_posts.html": "<html><body>fake</body></html>"},
    )

    with pytest.raises(ExportFormatError) as exc_info:
        parse_export(zip_path)

    message = str(exc_info.value).lower()
    assert "html" in message
    assert "json" in message  # tells the user to re-export as JSON


def test_parse_export_not_a_zip_raises_friendly_error(tmp_path: Path) -> None:
    not_a_zip = tmp_path / "export.zip"
    not_a_zip.write_bytes(b"this is definitely not a zip file")

    with pytest.raises(ExportFormatError) as exc_info:
        parse_export(not_a_zip)

    assert "zip" in str(exc_info.value).lower()


def test_parse_export_missing_file_raises_friendly_error(tmp_path: Path) -> None:
    with pytest.raises(ExportFormatError):
        parse_export(tmp_path / "does_not_exist.zip")


def test_parse_export_corrupt_json_raises_friendly_error(tmp_path: Path) -> None:
    zip_path = _write_zip(
        tmp_path,
        "export.zip",
        {"your_instagram_activity/saved/saved_posts.json": "{not valid json"},
    )

    with pytest.raises(ExportFormatError) as exc_info:
        parse_export(zip_path)

    assert "json" in str(exc_info.value).lower()


def test_parse_export_own_posts_with_carousel_media(tmp_path: Path) -> None:
    posts_json = json.dumps(
        [
            {
                "title": "my caption here",
                "creation_timestamp": 1700000000,
                "media": [
                    {"uri": "media/posts/202301/photo1.jpg", "creation_timestamp": 1700000000},
                    {"uri": "media/posts/202301/photo2.jpg", "creation_timestamp": 1700000001},
                ],
            }
        ]
    )
    zip_path = _write_zip(
        tmp_path,
        "export.zip",
        {
            "your_instagram_activity/media/posts_1.json": posts_json,
            "media/posts/202301/photo1.jpg": b"\xff\xd8\xff fake jpeg bytes 1",
            "media/posts/202301/photo2.jpg": b"\xff\xd8\xff fake jpeg bytes 2",
        },
    )

    parsed = parse_export(zip_path)

    assert len(parsed.own_posts) == 1
    post = parsed.own_posts[0]
    assert post.caption == "my caption here"
    assert post.media_type == MediaType.CAROUSEL
    assert len(post.media_files) == 2
    assert all(m.zip_member_name is not None for m in post.media_files)


def test_parse_export_own_post_with_missing_media_keeps_metadata(tmp_path: Path) -> None:
    posts_json = json.dumps(
        [
            {
                "title": "orphaned caption",
                "creation_timestamp": 1700000000,
                "media": [{"uri": "media/posts/202301/missing.jpg", "creation_timestamp": 1700000000}],
            }
        ]
    )
    zip_path = _write_zip(
        tmp_path, "export.zip", {"your_instagram_activity/media/posts_1.json": posts_json}
    )

    parsed = parse_export(zip_path)

    assert len(parsed.own_posts) == 1
    assert parsed.own_posts[0].media_files[0].zip_member_name is None
    assert any("missing" in w.lower() or "media" in w.lower() for w in parsed.warnings)
