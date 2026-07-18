"""Tests for the /api/import/* endpoints: upload -> job creation -> DB
writes, job listing/fetching, the wrong-format friendly-failure path, and
404s for unknown jobs."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient


def _fake_export_bytes(entries: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "your_instagram_activity/saved/saved_posts.json",
            json.dumps({"saved_saved_media": entries}),
        )
    return buf.getvalue()


def test_upload_export_creates_running_or_done_job(client: TestClient) -> None:
    entries = [
        {
            "title": "someone",
            "string_list_data": [
                {"href": "https://www.instagram.com/p/ABC123abc/", "timestamp": 1700000000}
            ],
        }
    ]
    zip_bytes = _fake_export_bytes(entries)

    response = client.post(
        "/api/import/upload",
        files={"file": ("export.zip", zip_bytes, "application/zip")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "done"
    assert body["total_items"] == 1
    assert body["processed_items"] == 1


def test_upload_export_wrong_format_returns_failed_job_not_500(client: TestClient) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("unrelated/whatever.txt", "nothing useful here")

    response = client.post(
        "/api/import/upload",
        files={"file": ("export.zip", buf.getvalue(), "application/zip")},
    )

    # The endpoint itself succeeds (202) -- the *job* records the failure,
    # so the frontend can show it inline via job.status/error_message.
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"]


def test_upload_export_not_a_zip_returns_failed_job(client: TestClient) -> None:
    response = client.post(
        "/api/import/upload",
        files={"file": ("export.zip", b"not a zip file", "application/zip")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert "zip" in body["error_message"].lower()


def test_list_and_get_import_jobs(client: TestClient) -> None:
    zip_bytes = _fake_export_bytes(
        [
            {
                "title": "someone",
                "string_list_data": [
                    {"href": "https://www.instagram.com/p/DEF456def/", "timestamp": 1700000000}
                ],
            }
        ]
    )
    upload_response = client.post(
        "/api/import/upload",
        files={"file": ("export.zip", zip_bytes, "application/zip")},
    )
    job_id = upload_response.json()["id"]

    list_response = client.get("/api/import/jobs")
    assert list_response.status_code == 200
    jobs = list_response.json()["jobs"]
    assert any(j["id"] == job_id for j in jobs)

    get_response = client.get(f"/api/import/jobs/{job_id}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == job_id


def test_get_import_job_404_for_unknown_id(client: TestClient) -> None:
    response = client.get("/api/import/jobs/999999")
    assert response.status_code == 404


def test_cancel_import_job_404_for_unknown_id(client: TestClient) -> None:
    response = client.post("/api/import/jobs/999999/cancel")
    assert response.status_code == 404


def test_cancel_already_finished_job_is_a_noop(client: TestClient) -> None:
    zip_bytes = _fake_export_bytes(
        [
            {
                "title": "someone",
                "string_list_data": [
                    {"href": "https://www.instagram.com/p/GHI789ghi/", "timestamp": 1700000000}
                ],
            }
        ]
    )
    upload_response = client.post(
        "/api/import/upload",
        files={"file": ("export.zip", zip_bytes, "application/zip")},
    )
    job_id = upload_response.json()["id"]

    cancel_response = client.post(f"/api/import/jobs/{job_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "done"  # unchanged, already finished


def test_sample_fixture_zip_uploads_successfully(client: TestClient) -> None:
    fixture_path = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sample_export.zip"
    assert fixture_path.is_file(), "expected repo-level tests/fixtures/sample_export.zip to exist"

    with fixture_path.open("rb") as f:
        response = client.post(
            "/api/import/upload",
            files={"file": ("sample_export.zip", f, "application/zip")},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "done"
    assert body["total_items"] == 3
