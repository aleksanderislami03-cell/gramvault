"""Tests for the /api/library/* endpoints: filterable item listing,
single-item detail, authors/tags listing, and manual tag updates.

Seeds the DB directly via `tmp_config`'s session_scope rather than going
through the ingestion pipeline, since these tests are about the query/
filter logic, not import parsing (see test_ingestion_importer.py for
that).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gramvault.config import Config
from gramvault.db.session import session_scope


def _seed_library(config: Config) -> dict[str, int]:
    with session_scope(config) as conn:
        conn.execute("INSERT INTO authors (username, full_name) VALUES ('alice', 'Alice A')")
        alice_id = conn.execute(
            "SELECT id FROM authors WHERE username = 'alice'"
        ).fetchone()["id"]
        conn.execute("INSERT INTO authors (username) VALUES ('bob')")
        bob_id = conn.execute("SELECT id FROM authors WHERE username = 'bob'").fetchone()["id"]

        conn.execute(
            "INSERT INTO items (external_id, author_id, media_type, caption, taken_at) "
            "VALUES ('item-1', ?, 'photo', 'a sunny beach day', '2024-01-01T00:00:00')",
            (alice_id,),
        )
        item1_id = conn.execute(
            "SELECT id FROM items WHERE external_id = 'item-1'"
        ).fetchone()["id"]

        conn.execute(
            "INSERT INTO items (external_id, author_id, media_type, caption, taken_at) "
            "VALUES ('item-2', ?, 'reel', 'city nightlife video', '2024-06-01T00:00:00')",
            (bob_id,),
        )
        item2_id = conn.execute(
            "SELECT id FROM items WHERE external_id = 'item-2'"
        ).fetchone()["id"]

        conn.execute(
            "INSERT INTO media_files (item_id, file_path, media_type, sequence_index) "
            "VALUES (?, 'media/aa/aaaa.jpg', 'photo', 0)",
            (item1_id,),
        )

        conn.execute("INSERT INTO tags (name, kind) VALUES ('travel', 'auto')")
        travel_tag_id = conn.execute(
            "SELECT id FROM tags WHERE name = 'travel'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)", (item1_id, travel_tag_id)
        )

    return {"alice_id": alice_id, "bob_id": bob_id, "item1_id": item1_id, "item2_id": item2_id}


def test_list_items_no_filters_returns_all(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get("/api/library/items")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_list_items_filter_by_author(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get("/api/library/items", params={"author": "alice"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "item-1"
    assert body["items"][0]["author"]["username"] == "alice"
    assert len(body["items"][0]["media_files"]) == 1
    assert len(body["items"][0]["tags"]) == 1
    assert body["items"][0]["tags"][0]["name"] == "travel"


def test_list_items_filter_by_media_type(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get("/api/library/items", params={"media_type": "reel"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "item-2"


def test_list_items_filter_by_tag(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get("/api/library/items", params={"tag": "travel"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "item-1"


def test_list_items_free_text_search(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get("/api/library/items", params={"q": "nightlife"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "item-2"


def test_list_items_date_range_filter(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get(
        "/api/library/items",
        params={"date_from": "2024-05-01T00:00:00", "date_to": "2024-12-31T00:00:00"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["external_id"] == "item-2"


def test_list_items_pagination(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get("/api/library/items", params={"page": 1, "page_size": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["page"] == 1
    assert body["page_size"] == 1


def test_get_item_returns_full_detail(client: TestClient, tmp_config: Config) -> None:
    ids = _seed_library(tmp_config)

    response = client.get(f"/api/library/items/{ids['item1_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["external_id"] == "item-1"
    assert body["author"]["username"] == "alice"


def test_get_item_404_for_unknown_id(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get("/api/library/items/999999")

    assert response.status_code == 404


def test_list_authors(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get("/api/library/authors")

    assert response.status_code == 200
    usernames = {a["username"] for a in response.json()}
    assert usernames == {"alice", "bob"}


def test_list_tags(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.get("/api/library/tags")

    assert response.status_code == 200
    names = {t["name"] for t in response.json()}
    assert names == {"travel"}


def test_update_item_tags_replaces_manual_tags_only(
    client: TestClient, tmp_config: Config
) -> None:
    ids = _seed_library(tmp_config)

    response = client.patch(
        f"/api/library/items/{ids['item1_id']}/tags", json={"tags": ["favorites", "summer"]}
    )

    assert response.status_code == 200
    body = response.json()
    tag_names = {t["name"] for t in body["tags"]}
    # The auto tag 'travel' survives a manual-tags update; the new manual
    # tags are added alongside it.
    assert tag_names == {"travel", "favorites", "summer"}


def test_update_item_tags_404_for_unknown_item(client: TestClient, tmp_config: Config) -> None:
    _seed_library(tmp_config)

    response = client.patch("/api/library/items/999999/tags", json={"tags": ["x"]})

    assert response.status_code == 404
