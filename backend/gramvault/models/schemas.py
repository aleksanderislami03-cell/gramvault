"""Shared Pydantic models for GramVault.

This is the contract other agents build on top of:
  - Agent A2 (ingestion) produces Author / Item / MediaFile / ImportJob rows.
  - Agent A3 (AI pipeline) fills in MediaFile.transcript / vision_caption and
    flips Item.enrichment_status, and writes embeddings to ChromaDB keyed by
    item/media_file id (collection layout is A3's call).
  - Agent A4 (chat/search) produces ChatSession / ChatMessage / ChatCitation.
  - Agent A5 (frontend) consumes all of these as JSON over the API in
    backend/gramvault/api/.
  - Agent A6 (Obsidian exporter) reads Item/MediaFile/Tag to render notes.

Keep field additions backwards compatible (add optional fields with
defaults) so agents working in parallel don't break each other.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MediaType(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    REEL = "reel"
    CAROUSEL = "carousel"


class FileMediaType(StrEnum):
    """Media type for an individual file on disk (a carousel Item is made
    of several PHOTO/VIDEO MediaFile rows)."""

    PHOTO = "photo"
    VIDEO = "video"


class JobStatus(StrEnum):
    """Shared status enum for long-running jobs (import, export)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class EnrichmentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TagKind(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    HASHTAG = "hashtag"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ORMBase(BaseModel):
    """Base class enabling `model_validate` from sqlite3.Row / ORM objects
    via attribute access."""

    model_config = ConfigDict(from_attributes=True)


class Author(ORMBase):
    id: int | None = None
    username: str
    full_name: str | None = None
    profile_url: str | None = None
    avatar_path: str | None = None
    created_at: datetime | None = None


class Tag(ORMBase):
    id: int | None = None
    name: str
    kind: TagKind = TagKind.AUTO


class MediaFile(ORMBase):
    id: int | None = None
    item_id: int
    file_path: str
    media_type: FileMediaType
    sequence_index: int = 0
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    # TODO(A3): populated by faster-whisper for videos/reels.
    transcript: str | None = None
    # TODO(A3): populated by the llava vision model.
    vision_caption: str | None = None
    checksum: str | None = None


class Item(ORMBase):
    id: int | None = None
    external_id: str | None = None
    author: Author | None = None
    media_type: MediaType
    caption: str | None = None
    permalink: str | None = None
    taken_at: datetime | None = None
    imported_at: datetime | None = None
    import_job_id: int | None = None
    enrichment_status: EnrichmentStatus = EnrichmentStatus.PENDING
    tags: list[Tag] = Field(default_factory=list)
    media_files: list[MediaFile] = Field(default_factory=list)


class ImportJob(ORMBase):
    id: int | None = None
    source_path: str
    status: JobStatus = JobStatus.PENDING
    total_items: int = 0
    processed_items: int = 0
    failed_items: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def progress_pct(self) -> float:
        """0-100 progress based on processed_items/total_items. Safe when
        total_items is 0 (job not yet sized) -> returns 0.0."""
        if self.total_items <= 0:
            return 0.0
        return round(100 * self.processed_items / self.total_items, 1)


class ChatCitation(ORMBase):
    id: int | None = None
    message_id: int | None = None
    item_id: int
    media_file_id: int | None = None
    snippet: str | None = None


class ChatMessage(ORMBase):
    id: int | None = None
    session_id: int
    role: ChatRole
    content: str
    created_at: datetime | None = None
    citations: list[ChatCitation] = Field(default_factory=list)


class ChatSession(ORMBase):
    id: int | None = None
    title: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
