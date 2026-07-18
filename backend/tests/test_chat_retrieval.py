"""Tests for `gramvault.chat.retrieval`: keyword search, vector-result
mapping, merge/rerank, and item resolution.

Ollama and ChromaDB are mocked throughout — no real Ollama server or Chroma
instance is required.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from gramvault.ai.embedding_store import QueryResult
from gramvault.chat import retrieval
from gramvault.chat.retrieval import RetrievalResult


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _insert_item(
    conn: sqlite3.Connection,
    *,
    caption: str | None = None,
    author_username: str = "someone",
    tags: list[str] | None = None,
    transcript: str | None = None,
    vision_caption: str | None = None,
) -> int:
    author_row = conn.execute(
        "SELECT id FROM authors WHERE username = ?", (author_username,)
    ).fetchone()
    if author_row is None:
        author_id = conn.execute(
            "INSERT INTO authors (username) VALUES (?)", (author_username,)
        ).lastrowid
    else:
        author_id = author_row["id"]

    item_id = conn.execute(
        "INSERT INTO items (author_id, media_type, caption) VALUES (?, 'photo', ?)",
        (author_id, caption),
    ).lastrowid

    if transcript is not None or vision_caption is not None:
        conn.execute(
            "INSERT INTO media_files (item_id, file_path, media_type, transcript, vision_caption) "
            "VALUES (?, 'x.jpg', 'photo', ?, ?)",
            (item_id, transcript, vision_caption),
        )

    for tag_name in tags or []:
        tag_row = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
        tag_id = (
            tag_row["id"]
            if tag_row is not None
            else conn.execute("INSERT INTO tags (name) VALUES (?)", (tag_name,)).lastrowid
        )
        conn.execute("INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)", (item_id, tag_id))

    conn.commit()
    return item_id


class TestKeywordSearch:
    def test_matches_caption(self, tmp_db_conn: sqlite3.Connection) -> None:
        item_id = _insert_item(tmp_db_conn, caption="An amazing pasta recipe")
        _insert_item(tmp_db_conn, caption="Something about hiking")

        results = retrieval.keyword_search(tmp_db_conn, "pasta")

        assert [r.item_id for r in results] == [item_id]
        assert results[0].sources == {"keyword"}
        assert results[0].score > 0

    def test_matches_tag_and_caption_scores_higher(self, tmp_db_conn: sqlite3.Connection) -> None:
        both = _insert_item(tmp_db_conn, caption="pasta night", tags=["pasta"])
        caption_only = _insert_item(tmp_db_conn, caption="pasta again", author_username="other")

        results = retrieval.keyword_search(tmp_db_conn, "pasta")
        by_id = {r.item_id: r for r in results}

        assert by_id[both].score > by_id[caption_only].score

    def test_matches_author_username(self, tmp_db_conn: sqlite3.Connection) -> None:
        item_id = _insert_item(tmp_db_conn, caption=None, author_username="chef.mia")

        results = retrieval.keyword_search(tmp_db_conn, "chef.mia")

        assert [r.item_id for r in results] == [item_id]

    def test_matches_transcript_and_vision_caption(self, tmp_db_conn: sqlite3.Connection) -> None:
        item_id = _insert_item(
            tmp_db_conn,
            caption=None,
            transcript="talking about kayaking on the lake",
            vision_caption=None,
        )

        results = retrieval.keyword_search(tmp_db_conn, "kayaking")

        assert [r.item_id for r in results] == [item_id]

    def test_empty_query_returns_nothing(self, tmp_db_conn: sqlite3.Connection) -> None:
        _insert_item(tmp_db_conn, caption="pasta")
        assert retrieval.keyword_search(tmp_db_conn, "   ") == []

    def test_no_match_returns_empty(self, tmp_db_conn: sqlite3.Connection) -> None:
        _insert_item(tmp_db_conn, caption="pasta")
        assert retrieval.keyword_search(tmp_db_conn, "zzz_nomatch") == []


class TestVectorSearch:
    def test_maps_query_results_to_retrieval_results(self) -> None:
        fake_results = [
            QueryResult(item_id=1, media_file_id=None, score=0.9, snippet="a", metadata={}),
            QueryResult(item_id=2, media_file_id=5, score=0.4, snippet="b", metadata={}),
        ]
        with patch.object(retrieval.embedding_store, "query", return_value=fake_results) as mock_query:
            results = retrieval.vector_search([0.1, 0.2], top_k=5)

        mock_query.assert_called_once()
        assert [r.item_id for r in results] == [1, 2]
        assert results[0].sources == {"vector"}
        assert results[0].score == 0.9


class TestMergeResults:
    def test_dedupes_and_boosts_items_in_both_sets(self) -> None:
        vector_results = [
            RetrievalResult(item_id=1, score=0.9, sources={"vector"}),
            RetrievalResult(item_id=2, score=0.5, sources={"vector"}),
        ]
        keyword_results = [
            RetrievalResult(item_id=2, score=0.6, sources={"keyword"}),
            RetrievalResult(item_id=3, score=0.8, sources={"keyword"}),
        ]

        merged = retrieval.merge_results(vector_results, keyword_results, top_k=10)
        by_id = {r.item_id: r for r in merged}

        # item 2 present in both -> boosted score and pulled towards the top
        assert by_id[2].sources == {"vector", "keyword"}
        assert by_id[2].score > 0.6
        assert merged[0].item_id == 2

        assert set(by_id) == {1, 2, 3}

    def test_respects_top_k(self) -> None:
        vector_results = [RetrievalResult(item_id=i, score=1.0 - i * 0.1, sources={"vector"}) for i in range(5)]
        merged = retrieval.merge_results(vector_results, [], top_k=2)
        assert len(merged) == 2

    def test_keyword_only_results_included(self) -> None:
        keyword_results = [RetrievalResult(item_id=9, score=0.5, sources={"keyword"})]
        merged = retrieval.merge_results([], keyword_results, top_k=10)
        assert [r.item_id for r in merged] == [9]


class TestHybridSearch:
    @pytest.mark.anyio
    async def test_combines_vector_and_keyword(self, tmp_db_conn: sqlite3.Connection) -> None:
        item_id = _insert_item(tmp_db_conn, caption="a pasta recipe", tags=["pasta"])
        other_id = _insert_item(tmp_db_conn, caption="unrelated hiking post", author_username="other")

        fake_vector_results = [
            QueryResult(item_id=other_id, media_file_id=None, score=0.7, snippet=None, metadata={}),
        ]
        with (
            patch.object(retrieval.ollama_client, "embed", new_callable=AsyncMock) as mock_embed,
            patch.object(retrieval.embedding_store, "query", return_value=fake_vector_results),
        ):
            mock_embed.return_value = [0.1, 0.2, 0.3]
            results = await retrieval.hybrid_search(tmp_db_conn, "pasta", top_k=5)

        mock_embed.assert_awaited_once()
        item_ids = {r.item_id for r in results}
        assert item_id in item_ids  # keyword match
        assert other_id in item_ids  # vector match


class TestFetchItems:
    def test_resolves_author_media_and_tags(self, tmp_db_conn: sqlite3.Connection) -> None:
        item_id = _insert_item(
            tmp_db_conn,
            caption="hello world",
            author_username="chef.mia",
            tags=["food", "pasta"],
            vision_caption="a plate of pasta",
        )

        items = retrieval.fetch_items(tmp_db_conn, [item_id])

        assert set(items) == {item_id}
        item = items[item_id]
        assert item.caption == "hello world"
        assert item.author is not None
        assert item.author.username == "chef.mia"
        assert {t.name for t in item.tags} == {"food", "pasta"}
        assert len(item.media_files) == 1
        assert item.media_files[0].vision_caption == "a plate of pasta"

    def test_empty_input_returns_empty_dict(self, tmp_db_conn: sqlite3.Connection) -> None:
        assert retrieval.fetch_items(tmp_db_conn, []) == {}
