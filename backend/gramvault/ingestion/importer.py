"""Import orchestration: parse a ZIP export, organize any embedded media,
and write authors/items/media_files rows — all tracked through an
`import_jobs` row (pending -> running -> done/failed) so progress can be
polled while it runs and so a re-run on the same ZIP is a cheap no-op for
items already imported.

This module is the single implementation shared by both:
  - `POST /api/import/upload` (`gramvault.api.routes_import`)
  - `gramvault import <zip>` (`gramvault.cli`)
so behavior never diverges between the two entry points.

Resumability: items are deduped by `external_id`, which is unique in the
`items` table. For saved (link-only) items this is the Instagram
shortcode parsed out of the post URL (or a stable hash of the URL if no
shortcode is present); for the user's own posts it's a stable hash of the
first media file's zip path (or of the caption+timestamp if no media
uri is available). Either way, re-running `run_import` against a job
whose items already exist in the DB skips them rather than erroring or
duplicating rows.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from gramvault.config import Config, get_config
from gramvault.db.session import session_scope
from gramvault.ingestion.organizer import organize_zip_member
from gramvault.ingestion.parser import ExportFormatError, OwnPost, SavedItem, parse_export
from gramvault.models.schemas import ImportJob, JobStatus

# --- import_jobs CRUD helpers (used by both this module and the API routes) --


def _row_to_job(row: sqlite3.Row) -> ImportJob:
    return ImportJob.model_validate(dict(row))


def create_import_job(zip_path: Path, config: Config | None = None) -> ImportJob:
    """Insert a new `import_jobs` row (status=pending) for `zip_path`."""
    config = config or get_config()
    with session_scope(config) as conn:
        cursor = conn.execute(
            "INSERT INTO import_jobs (source_path, status) VALUES (?, ?)",
            (str(zip_path), JobStatus.PENDING.value),
        )
        row = conn.execute(
            "SELECT * FROM import_jobs WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _row_to_job(row)


def get_import_job(job_id: int, config: Config | None = None) -> ImportJob | None:
    config = config or get_config()
    with session_scope(config) as conn:
        row = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row is not None else None


def list_import_jobs(config: Config | None = None) -> list[ImportJob]:
    config = config or get_config()
    with session_scope(config) as conn:
        rows = conn.execute(
            "SELECT * FROM import_jobs ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def cancel_import_job(job_id: int, config: Config | None = None) -> ImportJob | None:
    """Best-effort cancellation. Import runs synchronously in v1, so by the
    time this can be called the job has almost always already finished —
    this mainly exists so the endpoint/CLI has well-defined behavior once
    A3 (or a future version of this module) makes imports backgroundable."""
    config = config or get_config()
    with session_scope(config) as conn:
        row = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = _row_to_job(row)
        if job.status in (JobStatus.DONE, JobStatus.FAILED):
            return job
        conn.execute(
            "UPDATE import_jobs SET status = ?, error_message = ?, finished_at = datetime('now') "
            "WHERE id = ?",
            (JobStatus.FAILED.value, "cancelled by user", job_id),
        )
        row = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row)


# --- DB write helpers ------------------------------------------------------


def _get_or_create_author(conn: sqlite3.Connection, username: str | None) -> int | None:
    if not username:
        return None
    conn.execute("INSERT OR IGNORE INTO authors (username) VALUES (?)", (username,))
    row = conn.execute("SELECT id FROM authors WHERE username = ?", (username,)).fetchone()
    return row["id"] if row is not None else None


def _item_exists(conn: sqlite3.Connection, external_id: str | None) -> bool:
    if not external_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM items WHERE external_id = ?", (external_id,)
    ).fetchone()
    return row is not None


def _import_saved_item(conn: sqlite3.Connection, saved: SavedItem, job_id: int) -> None:
    if _item_exists(conn, saved.external_id):
        return  # already imported in a previous run of this (or another) job
    author_id = _get_or_create_author(conn, saved.author_username)
    conn.execute(
        """
        INSERT INTO items
            (external_id, author_id, media_type, caption, permalink, taken_at,
             import_job_id, raw_metadata_json)
        VALUES (?, ?, ?, NULL, ?, ?, ?, ?)
        """,
        (
            saved.external_id,
            author_id,
            saved.media_type_guess.value,
            saved.instagram_url,
            saved.saved_at.isoformat() if saved.saved_at else None,
            job_id,
            json.dumps(saved.raw, default=str),
        ),
    )
    # Note: `caption` is left NULL for saved (other users') items — the
    # saved-posts export only gives us a title/username, never the real
    # post caption, so we don't want to misrepresent one as the other.


def _import_own_post(
    conn: sqlite3.Connection,
    zf: zipfile.ZipFile,
    post: OwnPost,
    job_id: int,
    library_dir: Path,
) -> None:
    if _item_exists(conn, post.external_id):
        return
    cursor = conn.execute(
        """
        INSERT INTO items
            (external_id, author_id, media_type, caption, permalink, taken_at,
             import_job_id, raw_metadata_json)
        VALUES (?, NULL, ?, ?, NULL, ?, ?, ?)
        """,
        (
            post.external_id,
            post.media_type.value,
            post.caption,
            post.posted_at.isoformat() if post.posted_at else None,
            job_id,
            json.dumps(post.raw, default=str),
        ),
    )
    item_id = cursor.lastrowid
    for media in post.media_files:
        if media.zip_member_name is None:
            continue  # media bytes weren't found in this export; item metadata is still kept
        organized = organize_zip_member(zf, media.zip_member_name, library_dir)
        if organized is None:
            continue
        conn.execute(
            """
            INSERT INTO media_files
                (item_id, file_path, media_type, sequence_index, checksum)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                item_id,
                organized.file_path,
                media.file_type.value,
                media.sequence_index,
                organized.checksum,
            ),
        )


# --- orchestration ----------------------------------------------------------


def run_import(job_id: int, zip_path: Path, config: Config | None = None) -> ImportJob:
    """Run (or resume) the import for an existing `import_jobs` row.

    Safe to call more than once for the same job/ZIP: items already
    present (matched by `external_id`) are skipped rather than
    duplicated, and `processed_items`/`failed_items` are recomputed each
    run rather than accumulated across runs.
    """
    config = config or get_config()

    with session_scope(config) as conn:
        conn.execute(
            "UPDATE import_jobs SET status = ?, "
            "started_at = COALESCE(started_at, datetime('now')) WHERE id = ?",
            (JobStatus.RUNNING.value, job_id),
        )

    try:
        parsed = parse_export(zip_path)
    except ExportFormatError as exc:
        return fail_job(job_id, str(exc), config)
    except (zipfile.BadZipFile, OSError) as exc:
        return fail_job(job_id, f"Couldn't read '{zip_path}': {exc}", config)

    total = len(parsed.saved_items) + len(parsed.own_posts)
    with session_scope(config) as conn:
        conn.execute("UPDATE import_jobs SET total_items = ? WHERE id = ?", (total, job_id))

    processed = 0
    failed = 0

    with session_scope(config) as conn:
        for saved in parsed.saved_items:
            try:
                _import_saved_item(conn, saved, job_id)
            except Exception:
                failed += 1
            processed += 1
        conn.execute(
            "UPDATE import_jobs SET processed_items = ?, failed_items = ? WHERE id = ?",
            (processed, failed, job_id),
        )

    if parsed.own_posts:
        library_dir = config.resolved_library_dir
        library_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf, session_scope(config) as conn:
            for post in parsed.own_posts:
                try:
                    _import_own_post(conn, zf, post, job_id, library_dir)
                except Exception:
                    failed += 1
                processed += 1
            conn.execute(
                "UPDATE import_jobs SET processed_items = ?, failed_items = ? WHERE id = ?",
                (processed, failed, job_id),
            )

    final_status = (
        JobStatus.FAILED.value if total > 0 and failed == total else JobStatus.DONE.value
    )
    error_message = (
        "All items in this export failed to import — see server logs for details."
        if final_status == JobStatus.FAILED.value
        else None
    )
    with session_scope(config) as conn:
        conn.execute(
            "UPDATE import_jobs SET status = ?, error_message = ?, finished_at = datetime('now') "
            "WHERE id = ?",
            (final_status, error_message, job_id),
        )
        row = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row)


def fail_job(job_id: int, message: str, config: Config) -> ImportJob:
    with session_scope(config) as conn:
        conn.execute(
            "UPDATE import_jobs SET status = ?, error_message = ?, finished_at = datetime('now') "
            "WHERE id = ?",
            (JobStatus.FAILED.value, message, job_id),
        )
        row = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row)


def import_zip(zip_path: Path, config: Config | None = None) -> ImportJob:
    """Convenience wrapper: create a job, run it synchronously, return the
    final job state. This is the one function both the CLI and the API
    upload route call, so their behavior never diverges."""
    config = config or get_config()
    job = create_import_job(zip_path, config)
    assert job.id is not None
    return run_import(job.id, zip_path, config)
