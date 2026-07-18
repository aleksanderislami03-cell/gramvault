"""Tests for `gramvault.ai.pipeline`: per-item AI enrichment orchestration
and its resumability guarantees.

`ollama_client`, `keyframes`, `transcription`, and `embedding_store` are all
mocked at the module boundary — no real Ollama server, ffmpeg binary, or
whisper model is required.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from gramvault.ai import pipeline
from gramvault.ai.ollama_client import OllamaNotRunningError
from gramvault.ai.transcription import TranscriptionResult
from gramvault.chat.retrieval import fetch_items
from gramvault.config import Config
from gramvault.db.session import session_scope
from gramvault.models.schemas import EnrichmentStatus


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _insert_item(
    conn: sqlite3.Connection,
    *,
    media_type: str = "photo",
    caption: str | None = "a caption",
) -> int:
    item_id = conn.execute(
        "INSERT INTO items (media_type, caption) VALUES (?, ?)",
        (media_type, caption),
    ).lastrowid
    conn.commit()
    assert item_id is not None
    return item_id


def _insert_media_file(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    file_media_type: str = "photo",
    file_path: str = "photo.jpg",
    vision_caption: str | None = None,
    transcript: str | None = None,
    sequence_index: int = 0,
) -> int:
    media_file_id = conn.execute(
        """
        INSERT INTO media_files
            (item_id, file_path, media_type, sequence_index, vision_caption, transcript)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (item_id, file_path, file_media_type, sequence_index, vision_caption, transcript),
    ).lastrowid
    conn.commit()
    assert media_file_id is not None
    return media_file_id


def _get_item(config: Config, item_id: int):
    with session_scope(config) as conn:
        return fetch_items(conn, [item_id])[item_id]


class TestProcessItemPhoto:
    @pytest.mark.anyio
    async def test_full_enrichment_captions_photo_and_embeds(self, tmp_config: Config) -> None:
        with session_scope(tmp_config) as conn:
            item_id = _insert_item(conn, media_type="photo")
            _insert_media_file(conn, item_id, file_media_type="photo", file_path="a.jpg")

        with (
            patch.object(pipeline.ollama_client, "ensure_running", new_callable=AsyncMock),
            patch.object(pipeline.ollama_client, "ensure_model_pulled", new_callable=AsyncMock),
            patch.object(
                pipeline.ollama_client,
                "caption_image",
                new_callable=AsyncMock,
                return_value="a nice photo",
            ) as mock_caption,
            patch.object(pipeline.keyframes, "extract_keyframes") as mock_extract,
            patch.object(pipeline.transcription, "transcribe") as mock_transcribe,
            patch.object(
                pipeline.ollama_client, "embed", new_callable=AsyncMock, return_value=[0.1, 0.2]
            ) as mock_embed,
            patch.object(pipeline.embedding_store, "upsert_item") as mock_upsert,
        ):
            await pipeline.process_item(item_id, config=tmp_config)

        item = _get_item(tmp_config, item_id)
        assert item.enrichment_status == EnrichmentStatus.DONE
        assert item.media_files[0].vision_caption == "a nice photo"
        mock_caption.assert_awaited_once()
        mock_extract.assert_not_called()
        mock_transcribe.assert_not_called()
        mock_embed.assert_awaited()
        mock_upsert.assert_called()

    @pytest.mark.anyio
    async def test_no_content_document_skips_embedding(self, tmp_config: Config) -> None:
        # No caption, no author, no tags, no media files -> build_content_document
        # returns "" -> _embed_and_upsert should short-circuit without embedding.
        with session_scope(tmp_config) as conn:
            item_id = _insert_item(conn, media_type="photo", caption=None)

        with (
            patch.object(pipeline.ollama_client, "ensure_running", new_callable=AsyncMock),
            patch.object(pipeline.ollama_client, "ensure_model_pulled", new_callable=AsyncMock),
            patch.object(
                pipeline.ollama_client, "embed", new_callable=AsyncMock
            ) as mock_embed,
            patch.object(pipeline.embedding_store, "upsert_item") as mock_upsert,
        ):
            await pipeline.process_item(item_id, config=tmp_config)

        item = _get_item(tmp_config, item_id)
        assert item.enrichment_status == EnrichmentStatus.DONE
        mock_embed.assert_not_awaited()
        mock_upsert.assert_not_called()


class TestProcessItemVideo:
    @pytest.mark.anyio
    async def test_full_enrichment_extracts_keyframes_captions_and_transcribes(
        self, tmp_config: Config, tmp_path
    ) -> None:
        with session_scope(tmp_config) as conn:
            item_id = _insert_item(conn, media_type="video")
            _insert_media_file(conn, item_id, file_media_type="video", file_path="clip.mp4")

        fake_frames = [tmp_path / "frame1.jpg", tmp_path / "frame2.jpg"]
        fake_transcript = TranscriptionResult(text="someone talking about pasta")

        with (
            patch.object(pipeline.ollama_client, "ensure_running", new_callable=AsyncMock),
            patch.object(pipeline.ollama_client, "ensure_model_pulled", new_callable=AsyncMock),
            patch.object(
                pipeline.keyframes, "extract_keyframes", return_value=fake_frames
            ) as mock_extract,
            patch.object(
                pipeline.ollama_client,
                "caption_image",
                new_callable=AsyncMock,
                side_effect=["frame one caption", "frame two caption"],
            ) as mock_caption,
            patch.object(
                pipeline.transcription, "transcribe", return_value=fake_transcript
            ) as mock_transcribe,
            patch.object(
                pipeline.ollama_client, "embed", new_callable=AsyncMock, return_value=[0.1]
            ),
            patch.object(pipeline.embedding_store, "upsert_item") as mock_upsert,
        ):
            await pipeline.process_item(item_id, config=tmp_config)

        item = _get_item(tmp_config, item_id)
        assert item.enrichment_status == EnrichmentStatus.DONE
        media_file = item.media_files[0]
        assert media_file.vision_caption == "frame one caption | frame two caption"
        assert media_file.transcript == "someone talking about pasta"
        mock_extract.assert_called_once()
        assert mock_caption.await_count == 2
        mock_transcribe.assert_called_once()
        mock_upsert.assert_called()

    @pytest.mark.anyio
    async def test_video_with_no_frames_yields_empty_caption(
        self, tmp_config: Config
    ) -> None:
        with session_scope(tmp_config) as conn:
            item_id = _insert_item(conn, media_type="video")
            _insert_media_file(conn, item_id, file_media_type="video", file_path="clip.mp4")

        fake_transcript = TranscriptionResult(text="")

        with (
            patch.object(pipeline.ollama_client, "ensure_running", new_callable=AsyncMock),
            patch.object(pipeline.ollama_client, "ensure_model_pulled", new_callable=AsyncMock),
            patch.object(pipeline.keyframes, "extract_keyframes", return_value=[]),
            patch.object(
                pipeline.ollama_client, "caption_image", new_callable=AsyncMock
            ) as mock_caption,
            patch.object(pipeline.transcription, "transcribe", return_value=fake_transcript),
            patch.object(pipeline.ollama_client, "embed", new_callable=AsyncMock),
            patch.object(pipeline.embedding_store, "upsert_item"),
        ):
            await pipeline.process_item(item_id, config=tmp_config)

        item = _get_item(tmp_config, item_id)
        assert item.media_files[0].vision_caption == ""
        mock_caption.assert_not_awaited()


class TestResumability:
    @pytest.mark.anyio
    async def test_skips_already_captioned_video_but_still_transcribes(
        self, tmp_config: Config
    ) -> None:
        with session_scope(tmp_config) as conn:
            item_id = _insert_item(conn, media_type="video")
            _insert_media_file(
                conn,
                item_id,
                file_media_type="video",
                file_path="clip.mp4",
                vision_caption="already captioned",
                transcript=None,
            )

        fake_transcript = TranscriptionResult(text="new transcript")

        with (
            patch.object(pipeline.ollama_client, "ensure_running", new_callable=AsyncMock),
            patch.object(pipeline.ollama_client, "ensure_model_pulled", new_callable=AsyncMock),
            patch.object(pipeline.keyframes, "extract_keyframes") as mock_extract,
            patch.object(
                pipeline.ollama_client, "caption_image", new_callable=AsyncMock
            ) as mock_caption,
            patch.object(
                pipeline.transcription, "transcribe", return_value=fake_transcript
            ) as mock_transcribe,
            patch.object(pipeline.ollama_client, "embed", new_callable=AsyncMock),
            patch.object(pipeline.embedding_store, "upsert_item"),
        ):
            await pipeline.process_item(item_id, config=tmp_config)

        # Already-done step (vision captioning) must NOT be redone.
        mock_extract.assert_not_called()
        mock_caption.assert_not_awaited()
        # Remaining step (transcription) must still run.
        mock_transcribe.assert_called_once()

        item = _get_item(tmp_config, item_id)
        assert item.media_files[0].vision_caption == "already captioned"
        assert item.media_files[0].transcript == "new transcript"
        assert item.enrichment_status == EnrichmentStatus.DONE

    @pytest.mark.anyio
    async def test_skips_already_captioned_photo_entirely(self, tmp_config: Config) -> None:
        with session_scope(tmp_config) as conn:
            item_id = _insert_item(conn, media_type="photo")
            _insert_media_file(
                conn,
                item_id,
                file_media_type="photo",
                file_path="a.jpg",
                vision_caption="already captioned",
            )

        with (
            patch.object(pipeline.ollama_client, "ensure_running", new_callable=AsyncMock),
            patch.object(pipeline.ollama_client, "ensure_model_pulled", new_callable=AsyncMock),
            patch.object(
                pipeline.ollama_client, "caption_image", new_callable=AsyncMock
            ) as mock_caption,
            patch.object(pipeline.ollama_client, "embed", new_callable=AsyncMock),
            patch.object(pipeline.embedding_store, "upsert_item"),
        ):
            await pipeline.process_item(item_id, config=tmp_config)

        mock_caption.assert_not_awaited()
        item = _get_item(tmp_config, item_id)
        assert item.media_files[0].vision_caption == "already captioned"
        assert item.enrichment_status == EnrichmentStatus.DONE

    @pytest.mark.anyio
    async def test_fully_done_item_does_not_require_vision_model_check(
        self, tmp_config: Config
    ) -> None:
        # needs_vision should be False when every media file already has a
        # vision_caption -> ensure_model_pulled should only be checked for
        # the embedding model, not the vision model.
        with session_scope(tmp_config) as conn:
            item_id = _insert_item(conn, media_type="photo")
            _insert_media_file(
                conn,
                item_id,
                file_media_type="photo",
                file_path="a.jpg",
                vision_caption="already captioned",
            )

        with (
            patch.object(pipeline.ollama_client, "ensure_running", new_callable=AsyncMock),
            patch.object(
                pipeline.ollama_client, "ensure_model_pulled", new_callable=AsyncMock
            ) as mock_ensure_pulled,
            patch.object(pipeline.ollama_client, "caption_image", new_callable=AsyncMock),
            patch.object(pipeline.ollama_client, "embed", new_callable=AsyncMock),
            patch.object(pipeline.embedding_store, "upsert_item"),
        ):
            await pipeline.process_item(item_id, config=tmp_config)

        checked_models = {call.args[0] for call in mock_ensure_pulled.await_args_list}
        assert checked_models == {tmp_config.models.embedding_model}


class TestProcessItemFailureHandling:
    @pytest.mark.anyio
    async def test_ollama_not_running_marks_item_failed_with_error_recorded(
        self, tmp_config: Config
    ) -> None:
        with session_scope(tmp_config) as conn:
            item_id = _insert_item(conn, media_type="photo")
            _insert_media_file(conn, item_id, file_media_type="photo", file_path="a.jpg")

        with patch.object(
            pipeline.ollama_client,
            "ensure_running",
            new_callable=AsyncMock,
            side_effect=OllamaNotRunningError("http://localhost:11434"),
        ):
            await pipeline.process_item(item_id, config=tmp_config)

        item = _get_item(tmp_config, item_id)
        assert item.enrichment_status == EnrichmentStatus.FAILED

        with session_scope(tmp_config) as conn:
            row = conn.execute(
                "SELECT raw_metadata_json FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        metadata = json.loads(row["raw_metadata_json"])
        assert "Ollama" in metadata["enrichment_error"]

    @pytest.mark.anyio
    async def test_successful_rerun_clears_stale_error(self, tmp_config: Config) -> None:
        with session_scope(tmp_config) as conn:
            item_id = _insert_item(conn, media_type="photo")
            _insert_media_file(conn, item_id, file_media_type="photo", file_path="a.jpg")
            conn.execute(
                "UPDATE items SET raw_metadata_json = ? WHERE id = ?",
                (json.dumps({"enrichment_error": "previous failure"}), item_id),
            )

        with (
            patch.object(pipeline.ollama_client, "ensure_running", new_callable=AsyncMock),
            patch.object(pipeline.ollama_client, "ensure_model_pulled", new_callable=AsyncMock),
            patch.object(
                pipeline.ollama_client,
                "caption_image",
                new_callable=AsyncMock,
                return_value="a caption",
            ),
            patch.object(pipeline.ollama_client, "embed", new_callable=AsyncMock),
            patch.object(pipeline.embedding_store, "upsert_item"),
        ):
            await pipeline.process_item(item_id, config=tmp_config)

        with session_scope(tmp_config) as conn:
            row = conn.execute(
                "SELECT raw_metadata_json FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        metadata = json.loads(row["raw_metadata_json"]) if row["raw_metadata_json"] else {}
        assert "enrichment_error" not in metadata

    @pytest.mark.anyio
    async def test_missing_item_id_is_a_noop(self, tmp_config: Config) -> None:
        # Should return quietly (logs a warning) rather than raising.
        await pipeline.process_item(999999, config=tmp_config)


class TestProcessItems:
    @pytest.mark.anyio
    async def test_processes_each_id_sequentially_in_order(self, tmp_config: Config) -> None:
        calls: list[int] = []

        async def fake_process_item(item_id: int, config: Config | None = None) -> None:
            calls.append(item_id)

        with patch.object(pipeline, "process_item", new=fake_process_item):
            await pipeline.process_items([3, 1, 2], config=tmp_config)

        assert calls == [3, 1, 2]

    @pytest.mark.anyio
    async def test_empty_list_does_nothing(self, tmp_config: Config) -> None:
        with patch.object(pipeline, "process_item", new_callable=AsyncMock) as mock_process:
            await pipeline.process_items([], config=tmp_config)
        mock_process.assert_not_awaited()


class TestResolveTargetItemIds:
    def test_explicit_ids_are_returned_verbatim(self, tmp_db_conn: sqlite3.Connection) -> None:
        assert pipeline.resolve_target_item_ids(tmp_db_conn, [5, 3]) == [5, 3]

    def test_none_resolves_to_all_pending_items(self, tmp_db_conn: sqlite3.Connection) -> None:
        pending_id = tmp_db_conn.execute(
            "INSERT INTO items (media_type, enrichment_status) VALUES ('photo', 'pending')"
        ).lastrowid
        tmp_db_conn.execute(
            "INSERT INTO items (media_type, enrichment_status) VALUES ('photo', 'done')"
        )
        tmp_db_conn.commit()

        result = pipeline.resolve_target_item_ids(tmp_db_conn, None)

        assert result == [pending_id]

    def test_none_with_no_pending_items_returns_empty(
        self, tmp_db_conn: sqlite3.Connection
    ) -> None:
        tmp_db_conn.execute(
            "INSERT INTO items (media_type, enrichment_status) VALUES ('photo', 'done')"
        )
        tmp_db_conn.commit()
        assert pipeline.resolve_target_item_ids(tmp_db_conn, None) == []


class TestMarkItemsPending:
    def test_flips_given_items_to_pending(self, tmp_db_conn: sqlite3.Connection) -> None:
        item_id = tmp_db_conn.execute(
            "INSERT INTO items (media_type, enrichment_status) VALUES ('photo', 'done')"
        ).lastrowid
        tmp_db_conn.commit()

        pipeline.mark_items_pending(tmp_db_conn, [item_id])
        tmp_db_conn.commit()

        row = tmp_db_conn.execute(
            "SELECT enrichment_status FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert row["enrichment_status"] == "pending"

    def test_empty_list_is_a_noop(self, tmp_db_conn: sqlite3.Connection) -> None:
        # Should not raise (e.g. no malformed "IN ()" SQL).
        pipeline.mark_items_pending(tmp_db_conn, [])
