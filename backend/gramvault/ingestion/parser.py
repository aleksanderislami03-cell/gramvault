"""Instagram data export ZIP parser.

Instagram's "Download Your Information" export format has been
reorganized across versions (folder names/nesting have changed more than
once), so this module deliberately does NOT hardcode one exact path.
Instead it glob-matches a small set of *candidate* patterns against every
member name in the ZIP and takes the first hit. If nothing matches, it
raises `ExportFormatError` with a clear, user-facing message describing
what was searched for and what was found instead (including a dedicated
message when the export looks like the legacy HTML format).

Two kinds of content are recognized:
  - "Saved" posts/reels of *other* users (`saved_posts.json` or similar).
    The official export for these is link-only metadata (author/url/
    timestamp) — there is no media to download for someone else's post,
    so these are imported as link-only references. This is expected,
    not an error.
  - The user's *own* posts (`posts_1.json`/`posts_2.json`/... or similar),
    which reference actual media files elsewhere in the ZIP.

Nothing in this module extracts files to disk — see `organizer.py` for
that (and for the zip-slip guard applied before reading any referenced
member).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from gramvault.models.schemas import FileMediaType, MediaType

# --- errors ------------------------------------------------------------


class ExportFormatError(Exception):
    """Raised when a ZIP doesn't look like a recognizable Instagram data
    export. Carries a friendly, actionable message — this is one of the
    explicitly-required friendly failure modes (alongside "wrong ZIP
    format" more generally), so keep messages here safe to show directly
    in the UI/CLI without any rewrapping."""


# --- candidate path patterns --------------------------------------------
# fnmatch's `*` matches any run of characters *including `/`* (fnmatch has
# no concept of path separators), so a single `*` already spans multiple
# directory levels. That's what makes the trailing broad patterns below an
# effective catch-all across reorganized folder layouts.
SAVED_POSTS_JSON_GLOBS = [
    "your_instagram_activity/saved/saved_posts.json",
    "*/your_instagram_activity/saved/saved_posts.json",
    "connections/saved/saved_posts.json",
    "*/connections/saved/saved_posts.json",
    "saved/saved_posts.json",
    "*saved_posts.json",  # broad fallback: matches at any depth/prefix
]

OWN_POSTS_JSON_GLOBS = [
    "your_instagram_activity/media/posts_*.json",
    "*/your_instagram_activity/media/posts_*.json",
    "content/posts_*.json",
    "*/content/posts_*.json",
    "*posts_1.json",
    "*posts_2.json",
    "*/media/posts_*.json",
]

# If we can't find JSON but these look present, the export is almost
# certainly the legacy HTML format.
SAVED_POSTS_HTML_GLOBS = [
    "*saved_posts.html",
    "*/saved/*.html",
]
GENERIC_HTML_GLOBS = [
    "*your_instagram_activity*.html",
    "*posts_1.html",
]

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_\-]+)")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}


# --- data shapes ---------------------------------------------------------


@dataclass
class SavedItem:
    """One entry from the saved-posts export: a link-only reference to
    another user's post (Instagram's export doesn't include the media
    bytes for other people's content)."""

    title: str | None
    author_username: str | None
    instagram_url: str | None
    external_id: str | None
    saved_at: datetime | None
    media_type_guess: MediaType
    raw: dict


@dataclass
class OwnMediaFile:
    """One media file referenced by one of the user's own posts."""

    uri: str | None
    zip_member_name: str | None  # None if not found in the archive
    file_type: FileMediaType
    sequence_index: int
    creation_timestamp: datetime | None


@dataclass
class OwnPost:
    """One of the user's own posts (may have several media files if it's
    a carousel)."""

    external_id: str
    caption: str | None
    posted_at: datetime | None
    media_type: MediaType
    media_files: list[OwnMediaFile]
    raw: dict


@dataclass
class ParsedExport:
    saved_items: list[SavedItem] = field(default_factory=list)
    own_posts: list[OwnPost] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- zip member matching -------------------------------------------------


def _normalize(name: str) -> str:
    return name.replace("\\", "/")


def _find_first_match(names: list[str], patterns: list[str]) -> str | None:
    for pattern in patterns:
        pattern_l = pattern.lower()
        for name in names:
            if fnmatch.fnmatchcase(_normalize(name).lower(), pattern_l):
                return name
    return None


def _find_all_matches(names: list[str], patterns: list[str]) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        pattern_l = pattern.lower()
        for name in names:
            if name in seen:
                continue
            if fnmatch.fnmatchcase(_normalize(name).lower(), pattern_l):
                matched.append(name)
                seen.add(name)
    return matched


def _is_unsafe_member_name(name: str) -> bool:
    """Zip-slip guard: reject member names that could escape the archive
    root if ever used to construct a filesystem path (absolute paths,
    Windows drive letters, or `..` traversal components)."""
    norm = _normalize(name)
    if norm.startswith("/"):
        return True
    first_segment = norm.split("/", 1)[0]
    if len(first_segment) == 2 and first_segment[1] == ":":  # e.g. "C:"
        return True
    return any(part == ".." for part in PurePosixPath(norm).parts)


# --- saved-posts parsing ---------------------------------------------------


def _iter_dict_list(data: object) -> list:
    """Instagram's saved-posts JSON root shape has varied: sometimes a
    bare list, sometimes a dict wrapping the list under one of a few
    known keys, sometimes some other single list-valued key. Handle all
    three defensively rather than assuming one exact shape."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("saved_saved_media", "saved_media", "saved_posts", "saved"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        for value in data.values():
            if isinstance(value, list):
                return value
    return []


def _derive_external_id(href: str | None) -> str | None:
    if not href:
        return None
    match = _SHORTCODE_RE.search(href)
    if match:
        return match.group(1)
    # No recognizable shortcode in the URL — fall back to a stable
    # synthetic id derived from the URL itself so re-imports still dedupe.
    return f"url:{hashlib.sha256(href.encode('utf-8')).hexdigest()[:16]}"


def _guess_media_type_from_url(href: str | None) -> MediaType:
    if href and ("/reel/" in href or "/reels/" in href):
        return MediaType.REEL
    # The saved-posts export alone can't disambiguate photo/video/carousel
    # for someone else's post — PHOTO is the best-effort default; A3's
    # enrichment pass (or a later re-check against the real media) can
    # correct this.
    return MediaType.PHOTO


def _clean_username(title: str | None) -> str | None:
    if title and _USERNAME_RE.match(title):
        return title
    return None


def _parse_saved_posts_json(raw_bytes: bytes, member_name: str) -> list[SavedItem]:
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExportFormatError(
            f"Found a saved-posts file at '{member_name}' but it isn't valid UTF-8 text, "
            "so it doesn't look like Instagram's JSON export. If you exported in HTML "
            "format, please re-request your data from Instagram and choose 'JSON'."
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExportFormatError(
            f"Found a saved-posts file at '{member_name}' but couldn't parse it as JSON "
            f"({exc}). The file may be corrupted, or this may be an older/newer export "
            "format than GramVault expects."
        ) from exc

    entries = _iter_dict_list(data)
    items: list[SavedItem] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        string_list = entry.get("string_list_data")
        first = string_list[0] if isinstance(string_list, list) and string_list else {}
        if not isinstance(first, dict):
            first = {}
        href = first.get("href")
        timestamp = first.get("timestamp")
        saved_at = (
            datetime.fromtimestamp(timestamp, tz=UTC)
            if isinstance(timestamp, (int, float))
            else None
        )
        title = entry.get("title") or None
        items.append(
            SavedItem(
                title=title,
                author_username=_clean_username(title),
                instagram_url=href,
                external_id=_derive_external_id(href),
                saved_at=saved_at,
                media_type_guess=_guess_media_type_from_url(href),
                raw=entry,
            )
        )
    return items


# --- own-posts parsing ------------------------------------------------------


def _resolve_zip_member(uri: str, names_lower_map: dict[str, str]) -> str | None:
    norm = _normalize(uri).lstrip("/")
    if _is_unsafe_member_name(norm):
        return None
    exact = names_lower_map.get(norm.lower())
    if exact is not None:
        return exact
    # The export's root folder naming varies across versions — fall back
    # to a suffix match against every member name in the archive.
    suffix = norm.lower()
    for lower_name, original in names_lower_map.items():
        if lower_name.endswith(suffix):
            return original
    return None


def _parse_own_posts(zf: zipfile.ZipFile, names: list[str]) -> tuple[list[OwnPost], list[str]]:
    warnings: list[str] = []
    matches = _find_all_matches(names, OWN_POSTS_JSON_GLOBS)
    if not matches:
        return [], warnings

    names_lower_map = {_normalize(n).lower(): n for n in names}
    posts: list[OwnPost] = []

    for member in matches:
        try:
            data = json.loads(zf.read(member).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            warnings.append(f"Skipped '{member}': couldn't parse as JSON ({exc}).")
            continue

        entries = _iter_dict_list(data) if not isinstance(data, list) else data
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            media_list = entry.get("media")
            if not isinstance(media_list, list) or not media_list:
                # Some export shapes inline a single media dict on the
                # post entry itself rather than nesting it under "media".
                media_list = [entry] if isinstance(entry.get("uri"), str) else []
            if not media_list:
                continue

            caption = entry.get("title") or None
            post_ts = entry.get("creation_timestamp")
            posted_at = (
                datetime.fromtimestamp(post_ts, tz=UTC)
                if isinstance(post_ts, (int, float))
                else None
            )

            media_files: list[OwnMediaFile] = []
            for idx, media in enumerate(media_list):
                if not isinstance(media, dict):
                    continue
                uri = media.get("uri")
                resolved = (
                    _resolve_zip_member(uri, names_lower_map) if isinstance(uri, str) else None
                )
                if isinstance(uri, str) and resolved is None:
                    warnings.append(
                        f"Post referenced media '{uri}' but that file wasn't found in the "
                        "archive; keeping the post's metadata but skipping the media file."
                    )
                ext = Path(uri).suffix.lower() if isinstance(uri, str) else ""
                file_type = (
                    FileMediaType.VIDEO if ext in _VIDEO_EXTENSIONS else FileMediaType.PHOTO
                )
                media_ts = media.get("creation_timestamp")
                media_files.append(
                    OwnMediaFile(
                        uri=uri if isinstance(uri, str) else None,
                        zip_member_name=resolved,
                        file_type=file_type,
                        sequence_index=idx,
                        creation_timestamp=(
                            datetime.fromtimestamp(media_ts, tz=UTC)
                            if isinstance(media_ts, (int, float))
                            else None
                        ),
                    )
                )

            if not media_files:
                continue

            media_type = (
                MediaType.CAROUSEL
                if len(media_files) > 1
                else (
                    MediaType.VIDEO
                    if media_files[0].file_type == FileMediaType.VIDEO
                    else MediaType.PHOTO
                )
            )

            # The own-posts export doesn't carry a shortcode, so derive a
            # stable synthetic id from the first media reference (or the
            # caption+timestamp if no media uri is available) so
            # re-imports of the same ZIP dedupe correctly.
            seed = media_files[0].uri or f"{caption}|{post_ts}"
            external_id = f"post:{hashlib.sha256(str(seed).encode('utf-8')).hexdigest()[:16]}"

            posts.append(
                OwnPost(
                    external_id=external_id,
                    caption=caption,
                    posted_at=posted_at,
                    media_type=media_type,
                    media_files=media_files,
                    raw=entry,
                )
            )

    return posts, warnings


# --- top-level entry point -------------------------------------------------


def parse_export(zip_path: Path) -> ParsedExport:
    """Parse an Instagram data export ZIP into a `ParsedExport`.

    Raises `ExportFormatError` (with a clear, user-facing message) if the
    file isn't a ZIP, or if no recognizable saved-posts/own-posts content
    can be found inside it.
    """
    if not zip_path.exists():
        raise ExportFormatError(f"No such file: {zip_path}")

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ExportFormatError(
            f"'{zip_path.name}' isn't a valid ZIP file ({exc}). Make sure you downloaded the "
            "complete 'Download Your Information' export from Instagram, and that the file "
            "wasn't corrupted or only partially downloaded."
        ) from exc

    with zf:
        names = zf.namelist()

        saved_member = _find_first_match(names, SAVED_POSTS_JSON_GLOBS)
        saved_items: list[SavedItem] = []
        if saved_member is not None:
            saved_items = _parse_saved_posts_json(zf.read(saved_member), saved_member)

        own_posts, warnings = _parse_own_posts(zf, names)

        if saved_member is None and not own_posts:
            html_member = _find_first_match(
                names, SAVED_POSTS_HTML_GLOBS + GENERIC_HTML_GLOBS
            )
            if html_member is not None:
                raise ExportFormatError(
                    "This looks like Instagram's older HTML export format (found "
                    f"'{html_member}'). GramVault only supports the JSON export format. "
                    "Please re-request your data from Instagram and choose 'JSON' under "
                    "'Format' when downloading (Accounts Center > Your information and "
                    "permissions > Download your information), then re-upload the new ZIP."
                )

            sample = ", ".join(names[:15]) + (" ..." if len(names) > 15 else "")
            raise ExportFormatError(
                "Couldn't find a recognizable saved-posts or posts file in this ZIP.\n"
                f"Searched for (among others): {', '.join(SAVED_POSTS_JSON_GLOBS[:3])}, ...\n"
                f"Found {len(names)} file(s) in the archive, e.g.: {sample or '(empty archive)'}\n"
                "Make sure this is an Instagram 'Download Your Information' export in JSON "
                "format, with the 'Saved' and/or 'Posts' categories included in the request."
            )

    return ParsedExport(saved_items=saved_items, own_posts=own_posts, warnings=warnings)
