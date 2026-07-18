"""Tests for gramvault.ingestion.importer: end-to-end import of a fake
ZIP into the DB (authors/items/media_files), progress tracking on the
import_jobs row, dedupe-on-reimport, and media organization/dedupe by
hash.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from gramvault.config import Config
from gramvault.ingestion.importer import import_zip
from gramvault.models.schemas import JobStatus


def _write_saved_export(tmp_path: Path, name: str = "export.zip") -> Path:
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
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "your_instagram_activity/saved/saved_posts.json",
            json.dumps({"saved_saved_media": entries}),
        )
    return zip_path


def _write_own_posts_export(tmp_path: Path, name: str = "own_export.zip") -> Path:
    posts_json = json.dumps(
        [
            {
                "title": "a carousel post",
                "creation_timestamp": 1700000000,
                "media": [
                    {"uri": "media/posts/202301/a.jpg", "creation_timestamp": 1700000000},
                    {"uri": "media/posts/202301/b.jpg", "creation_timestamp": 1700000001},
                ],
            }
        ]
    )
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("your_instagram_activity/media/posts_1.json", posts_json)
        zf.writestr("media/posts/202301/a.jpg", b"fake jpeg bytes AAAA")
        zf.writestr("media/posts/202301/b.jpg", b"fake jpeg bytes BBBB")
    return zip_path


def test_import_zip_creates_authors_and_items(tmp_path: Path, tmp_config: Config) -> None:
    zip_path = _write_saved_export(tmp_path)

    job = import_zip(zip_path, tmp_config)

    assert job.status == JobStatus.DONE
    assert job.total_items == 2
    assert job.processed_items == 2
    assert job.failed_items == 0

    from gramvault.db.session import session_scope

    with session_scope(tmp_config) as conn:
        authors = {row["username"] for row in conn.execute("SELECT username FROM authors")}
        items = conn.execute("SELECT external_id, author_id, caption FROM items").fetchall()

    assert authors == {"chef_alice", "traveler_bob"}
    assert len(items) == 2
    # Saved (other users') items are link-only: no real caption available.
    assert all(row["caption"] is None for row in items)


def test_reimport_same_zip_dedupes_items(tmp_path: Path, tmp_config: Config) -> None:
    zip_path = _write_saved_export(tmp_path)

    first_job = import_zip(zip_path, tmp_config)
    second_job = import_zip(zip_path, tmp_config)

    assert first_job.status == JobStatus.DONE
    assert second_job.status == JobStatus.DONE
    # Second run should still report the items as "processed" (skipped, not
    # duplicated) rather than erroring.
    assert second_job.processed_items == 2
    assert second_job.failed_items == 0

    from gramvault.db.session import session_scope

    with session_scope(tmp_config) as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]
        author_count = conn.execute("SELECT COUNT(*) AS c FROM authors").fetchone()["c"]

    assert count == 2  # not 4 -- no duplicates created
    assert author_count == 2


def test_import_own_posts_organizes_media_by_hash(tmp_path: Path, tmp_config: Config) -> None:
    zip_path = _write_own_posts_export(tmp_path)

    job = import_zip(zip_path, tmp_config)

    assert job.status == JobStatus.DONE
    assert job.total_items == 1
    assert job.processed_items == 1

    from gramvault.db.session import session_scope

    with session_scope(tmp_config) as conn:
        items = conn.execute("SELECT id, media_type, caption FROM items").fetchall()
        assert len(items) == 1
        assert items[0]["media_type"] == "carousel"
        assert items[0]["caption"] == "a carousel post"
        media_files = conn.execute(
            "SELECT file_path, checksum FROM media_files WHERE item_id = ? ORDER BY sequence_index",
            (items[0]["id"],),
        ).fetchall()

    assert len(media_files) == 2
    for row in media_files:
        media_path = tmp_config.resolved_library_dir / row["file_path"]
        assert media_path.is_file()
        assert row["checksum"] in row["file_path"]  # organized under a hash-based path


def test_reimport_own_posts_does_not_duplicate_media_bytes_on_disk(
    tmp_path: Path, tmp_config: Config
) -> None:
    zip_path = _write_own_posts_export(tmp_path)

    import_zip(zip_path, tmp_config)
    import_zip(zip_path, tmp_config)

    media_dir = tmp_config.resolved_library_dir / "media"
    all_files = [p for p in media_dir.rglob("*") if p.is_file()]
    # Exactly 2 unique files (a.jpg, b.jpg content), never duplicated.
    assert len(all_files) == 2


def test_import_job_status_transitions_and_progress(tmp_path: Path, tmp_config: Config) -> None:
    from gramvault.ingestion.importer import create_import_job, get_import_job, run_import

    zip_path = _write_saved_export(tmp_path)
    job = create_import_job(zip_path, tmp_config)
    assert job.status == JobStatus.PENDING
    assert job.id is not None

    finished = run_import(job.id, zip_path, tmp_config)
    assert finished.status == JobStatus.DONE
    assert finished.started_at is not None
    assert finished.finished_at is not None

    fetched = get_import_job(job.id, tmp_config)
    assert fetched is not None
    assert fetched.status == JobStatus.DONE
    assert fetched.progress_pct == 100.0


def test_import_bad_zip_marks_job_failed_with_friendly_message(
    tmp_path: Path, tmp_config: Config
) -> None:
    bad_zip = tmp_path / "bad_export.zip"
    bad_zip.write_bytes(b"not a real zip file at all")

    job = import_zip(bad_zip, tmp_config)

    assert job.status == JobStatus.FAILED
    assert job.error_message
    assert "zip" in job.error_message.lower()


def test_list_import_jobs_orders_most_recent_first(tmp_path: Path, tmp_config: Config) -> None:
    from gramvault.ingestion.importer import list_import_jobs

    zip_a = _write_saved_export(tmp_path, "a.zip")
    zip_b = _write_saved_export(tmp_path, "b.zip")
    job_a = import_zip(zip_a, tmp_config)
    job_b = import_zip(zip_b, tmp_config)

    jobs = list_import_jobs(tmp_config)

    assert jobs[0].id == job_b.id
    assert jobs[1].id == job_a.id
