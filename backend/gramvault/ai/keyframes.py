"""ffmpeg-based keyframe extraction for videos/reels.

Extracts up to `config.video.max_keyframes` frames, spaced
`config.video.keyframe_interval_seconds` seconds apart, from a video file.
These frames are what get captioned by the vision model (llava) in
`gramvault.ai.pipeline` since llava can't process video directly.

`ffmpeg` is shelled out to via `subprocess` (no Python ffmpeg binding
dependency). If the `ffmpeg` binary isn't on PATH, `FFmpegNotFoundError` is
raised with instructions for installing it — this is one of the project's
required "friendly failure" modes (checked with `shutil.which` up front so
the error is immediate and clear rather than a raw `FileNotFoundError`).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from gramvault.config import Config, get_config


class FFmpegNotFoundError(RuntimeError):
    """Raised when the `ffmpeg` binary can't be found on PATH."""

    def __init__(self) -> None:
        super().__init__(
            "ffmpeg was not found on your PATH, but it's required to extract "
            "keyframes from videos/reels for AI captioning.\n"
            "Install it, then try again:\n"
            "  Windows:  winget install ffmpeg  (or choco install ffmpeg)\n"
            "  macOS:    brew install ffmpeg\n"
            "  Linux:    apt install ffmpeg  (or your distro's equivalent)"
        )


class KeyframeExtractionError(RuntimeError):
    """Raised when `ffmpeg` runs but exits non-zero extracting frames."""

    def __init__(self, video_path: Path, stderr: str) -> None:
        super().__init__(
            f"ffmpeg failed to extract keyframes from {video_path}:\n{stderr.strip()}"
        )
        self.video_path = video_path
        self.stderr = stderr


def ffmpeg_available() -> bool:
    """Return True if the `ffmpeg` binary is discoverable on PATH."""
    return shutil.which("ffmpeg") is not None


def extract_keyframes(
    video_path: Path,
    output_dir: Path,
    config: Config | None = None,
) -> list[Path]:
    """Extract up to `config.video.max_keyframes` frames from `video_path`,
    one every `config.video.keyframe_interval_seconds` seconds, into
    `output_dir`. Returns the extracted frame paths in order.

    Raises `FFmpegNotFoundError` if ffmpeg isn't installed, or
    `KeyframeExtractionError` if ffmpeg fails on this specific file (e.g.
    corrupt/unreadable video).
    """
    config = config or get_config()
    if not ffmpeg_available():
        raise FFmpegNotFoundError()

    video_path = Path(video_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    interval = max(1, config.video.keyframe_interval_seconds)
    max_frames = max(1, config.video.max_keyframes)

    pattern = output_dir / f"{video_path.stem}_kf_%03d.jpg"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval}",
        "-frames:v",
        str(max_frames),
        "-qscale:v",
        "4",
        str(pattern),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise KeyframeExtractionError(video_path, result.stderr)

    frames = sorted(output_dir.glob(f"{video_path.stem}_kf_*.jpg"))
    return frames[:max_frames]
