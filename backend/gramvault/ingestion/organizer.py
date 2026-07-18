"""Media organizer: content-addressed storage for media files pulled out
of an Instagram export ZIP.

Design:
    Every media file we manage to locate in the ZIP is hashed (sha256) and
    written to `<library_dir>/media/<first-2-hex-chars>/<full-hash>.<ext>`.
    Writing is a no-op if the destination already exists, so re-importing
    the same ZIP (or a different export that happens to contain the same
    file) never duplicates bytes on disk — dedupe is purely by content,
    not by source path.

    We never call `ZipFile.extract`/`extractall` with an attacker/export
    -controlled destination path — the destination filename here is
    always derived from the hash we just computed, never from the zip
    member name. The only zip-slip-sensitive step (resolving a JSON
    "uri" string to an actual zip member to *read*) happens in
    `parser._resolve_zip_member`, which already rejects unsafe member
    names before this module ever sees them.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OrganizedMediaFile:
    file_path: str  # POSIX path, relative to library_dir, e.g. "media/ab/ab34...ef.jpg"
    checksum: str  # full sha256 hex digest


def _write_deduped(data: bytes, ext: str, library_dir: Path) -> OrganizedMediaFile:
    digest = hashlib.sha256(data).hexdigest()
    subdir = library_dir / "media" / digest[:2]
    subdir.mkdir(parents=True, exist_ok=True)
    dest = subdir / f"{digest}{ext}"
    if not dest.exists():
        dest.write_bytes(data)
    return OrganizedMediaFile(file_path=dest.relative_to(library_dir).as_posix(), checksum=digest)


def organize_zip_member(
    zf: zipfile.ZipFile, member_name: str, library_dir: Path
) -> OrganizedMediaFile | None:
    """Read `member_name` out of an already-open ZIP, hash it, and write it
    (deduped) under `library_dir/media/...`. Returns `None` if the member
    can't be read (missing/corrupt entry) rather than raising, so one bad
    media reference doesn't abort the whole import."""
    try:
        data = zf.read(member_name)
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    ext = Path(member_name).suffix.lower()
    return _write_deduped(data, ext, library_dir)
