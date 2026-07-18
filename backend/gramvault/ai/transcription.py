"""faster-whisper wrapper for audio/video transcription.

Used by `gramvault.ai.pipeline` to transcribe the audio track of
videos/reels for embedding + citation display. Runs fully locally (no
network calls) via the `faster-whisper` CTranslate2-based Whisper
implementation.

The whisper model itself is expensive to load, so it's cached per
(model size, device, compute type) in-process. The actual `faster_whisper`
import happens lazily inside `_load_model()` so that:
  - the module can be imported (and its pure functions unit-tested) even in
    environments where `faster-whisper` isn't installed yet, and
  - tests can monkeypatch `_load_model` directly instead of needing a real
    model file on disk.

Model size/device/compute type aren't in `config.yaml` (that file is owned
by Agent A1's scaffold and lists only the Ollama model names) — they're
read from environment variables with sensible CPU-friendly defaults so
they're still configurable without hardcoding, without needing to touch
`gramvault/config.py`:
    GRAMVAULT_WHISPER_MODEL_SIZE   (default: "base")
    GRAMVAULT_WHISPER_DEVICE       (default: "cpu")
    GRAMVAULT_WHISPER_COMPUTE_TYPE (default: "int8")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_MODEL_SIZE = "base"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"


def _whisper_model_size() -> str:
    return os.environ.get("GRAMVAULT_WHISPER_MODEL_SIZE", DEFAULT_MODEL_SIZE)


def _whisper_device() -> str:
    return os.environ.get("GRAMVAULT_WHISPER_DEVICE", DEFAULT_DEVICE)


def _whisper_compute_type() -> str:
    return os.environ.get("GRAMVAULT_WHISPER_COMPUTE_TYPE", DEFAULT_COMPUTE_TYPE)


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str | None = None


class WhisperNotAvailableError(RuntimeError):
    """Raised when the `faster-whisper` package isn't installed."""

    def __init__(self, cause: Exception | None = None) -> None:
        super().__init__(
            "The 'faster-whisper' package is not installed, but it's required "
            "to transcribe audio/video.\n"
            "Install it with: pip install faster-whisper\n"
            "(it should already be listed in pyproject.toml — try "
            "`pip install -e \".[dev]\"` from the repo root)."
        )
        self.cause = cause


@lru_cache(maxsize=1)
def _load_model(model_size: str, device: str, compute_type: str) -> Any:
    """Load (and cache) a faster-whisper `WhisperModel`. Separated into its
    own function so tests can monkeypatch it without a real model file."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch in tests
        raise WhisperNotAvailableError(exc) from exc
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def transcribe(media_path: Path) -> TranscriptionResult:
    """Transcribe the audio track of `media_path` (video or audio file).

    Returns an empty-text `TranscriptionResult` (not an error) if the file
    has no audio track or no speech is detected — that's an expected,
    common case (e.g. a muted video/reel), not a failure.
    """
    media_path = Path(media_path)
    model = _load_model(_whisper_model_size(), _whisper_device(), _whisper_compute_type())

    try:
        segments_iter, info = model.transcribe(str(media_path))
    except Exception:
        # faster-whisper/ffmpeg (used internally for audio decoding) can
        # raise a variety of exceptions for "no audio track"/corrupt media.
        # Treat any of them as "nothing to transcribe" rather than a hard
        # pipeline failure, per the task's "handle gracefully" requirement.
        return TranscriptionResult(text="")

    segments: list[TranscriptSegment] = [
        TranscriptSegment(start=float(seg.start), end=float(seg.end), text=seg.text.strip())
        for seg in segments_iter
    ]
    text = " ".join(seg.text for seg in segments if seg.text)
    language = getattr(info, "language", None)
    return TranscriptionResult(text=text, segments=segments, language=language)
