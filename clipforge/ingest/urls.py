"""Turning dropped or pasted text into sources: YouTube links, Google Drive links, local video files, other
sites (SPEC 7.1 and 8.1)."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import SplitResult, parse_qs, unquote, urlparse, urlsplit
from urllib.request import url2pathname

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_CLOCK = re.compile(r"(\d+):(\d{1,2})(?::(\d{1,2}))?")
_UNITS = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_BARE_DOMAIN = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?:[/?#]|$)")
_VIDEO_PATHS = ("shorts", "live", "embed", "v")
DRIVE_HOSTS = ("drive.google.com", "docs.google.com", "drive.usercontent.google.com")
_DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{28,}")
_DRIVE_FOLDER = re.compile(r"/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)")
_DRIVE_FILE = re.compile(r"/file/d/([A-Za-z0-9_-]+)")
_DRIVE_BY_ID = ("/open", "/uc", "/download")  # paths that name the file in ?id=
FOLDER_MAX = 50  # the most videos one Drive folder link adds

NOT_A_VIDEO = "That YouTube link isn't one video. Paste a single video link instead."
PLAYLIST = "That's a playlist. Paste a single video link instead."
OTHER_SITE = (
    'Only YouTube and Google Drive links work for now. Turn on "Allow other sites" in Settings to try '
    "this one."
)
NOT_WEB = "ClipForge can only open web links (http or https)."
NOT_FOUND = "File not found."
FOLDER = "That's a folder. Drop a video file instead."
NOT_VIDEO_FILE = "Not a video file. ClipForge takes .mp4, .mov, .mkv, .webm and .m4v files."
NOTHING = "Nothing to clip. Give a YouTube or Google Drive link, or the path of a video file."
DRIVE_NOT_A_FILE = (
    "That Google Drive link isn't a video file or a folder. Copy the link from Drive's Share button and "
    "paste that."
)
DRIVE_EMPTY = (
    "This Drive folder has no videos in it. ClipForge takes the videos directly in the folder, not the ones "
    "in folders inside it."
)
DRIVE_CUT = (
    "That folder has more than {n} videos. ClipForge added the first {n}; add the rest from a folder of "
    "their own."
)


@dataclass(frozen=True)
class SourceRef:
    kind: Literal["youtube", "drive", "drive_folder", "file", "other"]
    url: str | None
    video_id: str | None
    path: Path | None
    focus_s: float | None  # a moment the link points at (?t=), used to centre the search


@dataclass(frozen=True)
class Rejected:
    token: str
    reason: str


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def drive_file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def drive_folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def video_title(name: str) -> str:
    """A file's name without its video extension, which is how Drive names a video."""
    stem, dot, ext = name.rpartition(".")
    return stem if dot and stem and f".{ext.lower()}" in VIDEO_EXTS else name


def file_from_url(url: str) -> Path:
    """The file a file:// link points at, which is how a project made from a file keeps its source."""
    return Path(url2pathname(urlparse(url).path))


def parse_timestamp(value: str) -> float | None:
    """ "90", "90s", "1m30s", "1h2m3s", "1:30" and "01:02:03" are all seconds; anything else is None."""
    value = value.strip().lower()
    if clock := _CLOCK.fullmatch(value):
        a, b, c = clock.groups()
        if c is None:
            return float(int(a) * 60 + int(b))
        return float(int(a) * 3600 + int(b) * 60 + int(c))
    units = _UNITS.fullmatch(value)
    if not units or not any(units.groups()):
        return None
    h, m, s = units.groups()
    return int(h or 0) * 3600 + int(m or 0) * 60 + float(s or 0)


def _split_url(token: str) -> SplitResult:
    return urlsplit(token if _SCHEME.match(token) else "https://" + token)


def _is_youtube(parts: SplitResult) -> bool:
    host = (parts.hostname or "").removeprefix("www.")
    return host in ("youtube.com", "youtu.be", "youtube-nocookie.com") or host.endswith(".youtube.com")


def _video_id(parts: SplitResult) -> str | None:
    host = (parts.hostname or "").removeprefix("www.")
    segments = [s for s in parts.path.split("/") if s]
    if host == "youtu.be":
        candidate = segments[0] if segments else None
    elif segments[:1] == ["watch"]:
        candidate = parse_qs(parts.query).get("v", [None])[0]
    elif len(segments) >= 2 and segments[0] in _VIDEO_PATHS:
        candidate = segments[1]
    else:
        candidate = None
    return candidate if candidate and _ID.fullmatch(candidate) else None


def _focus(parts: SplitResult) -> float | None:
    for params in (parse_qs(parts.query), parse_qs(parts.fragment)):
        for key in ("t", "start"):
            if key in params:
                seconds = parse_timestamp(params[key][0])
                return seconds or None  # t=0 is the start of the video, not a moment in it
    return None


def _is_drive(parts: SplitResult) -> bool:
    return (parts.hostname or "") in DRIVE_HOSTS


def is_drive(url: str) -> bool:
    return _is_drive(_split_url(url.strip()))


def _drive_file_id(parts: SplitResult) -> str | None:
    """The file a Drive link names, whichever of Drive's link shapes it came in."""
    if found := _DRIVE_FILE.match(parts.path):
        file_id = found[1]
    elif parts.path.rstrip("/") in _DRIVE_BY_ID:
        file_id = parse_qs(parts.query).get("id", [""])[0]
    else:
        return None
    return file_id if _DRIVE_ID.fullmatch(file_id) else None


def drive_file_id(url: str) -> str | None:
    parts = _split_url(url.strip())
    return _drive_file_id(parts) if _is_drive(parts) else None


def _drive(token: str, parts: SplitResult) -> SourceRef | Rejected:
    """A Drive file or folder link as one source."""
    if (folder := _DRIVE_FOLDER.match(parts.path)) and _DRIVE_ID.fullmatch(folder[1]):
        return SourceRef("drive_folder", drive_folder_url(folder[1]), folder[1], None, None)
    if file_id := _drive_file_id(parts):
        return SourceRef("drive", drive_file_url(file_id), file_id, None, None)
    return Rejected(token, DRIVE_NOT_A_FILE)


def youtube_id(url: str) -> str | None:
    parts = _split_url(url.strip())
    return _video_id(parts) if _is_youtube(parts) else None


# Tokens


def _whole_line_path(line: str) -> str | None:
    """A line that is one existing path, spaces and all (a dragged file, or one CLI argument)."""
    candidate = line
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "'\"":
        candidate = candidate[1:-1]
    candidate = candidate.replace("\\ ", " ")
    if candidate.startswith(("/", "~")) and Path(candidate).expanduser().exists():
        return candidate
    return None


def _split_line(line: str) -> list[str]:
    lex = shlex.shlex(line, posix=True)
    lex.whitespace_split = True
    lex.whitespace = " \t\r\n,"
    lex.commenters = ""  # a "#t=90" fragment is not a comment
    try:
        return list(lex)
    except ValueError:  # an unpaired quote, as in "it's"
        return [t for t in re.split(r"[\s,]+", line) if t]


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        whole = _whole_line_path(line)
        tokens.extend([whole] if whole else _split_line(line))
    return tokens


# Classifying one token


def _file(token: str, path: Path) -> SourceRef | Rejected:
    path = path.expanduser().resolve()
    if not path.exists():
        return Rejected(token, NOT_FOUND)
    if path.is_dir():
        return Rejected(token, FOLDER)
    if path.suffix.lower() not in VIDEO_EXTS:
        return Rejected(token, NOT_VIDEO_FILE)
    return SourceRef("file", None, None, path, None)


def _url(token: str, allow_other_sites: bool) -> SourceRef | Rejected:
    link = token.rstrip(".,;:!?)]>\"'")  # sentence punctuation and closing brackets after a link
    parts = _split_url(link)
    if parts.scheme.lower() not in ("http", "https"):
        return Rejected(token, NOT_WEB)
    if _is_youtube(parts):
        video_id = _video_id(parts)
        if video_id:
            return SourceRef("youtube", canonical_url(video_id), video_id, None, _focus(parts))
        if parts.path.rstrip("/") == "/playlist":
            return Rejected(token, PLAYLIST)
        return Rejected(token, NOT_A_VIDEO)
    if _is_drive(parts):
        return _drive(token, parts)  # Drive needs no Allow other sites: briefs send their videos this way
    if allow_other_sites:
        return SourceRef("other", parts.geturl(), None, None, None)
    return Rejected(token, OTHER_SITE)


def _classify(token: str, allow_other_sites: bool) -> SourceRef | Rejected | None:
    """None for plain words around the links, which are ignored."""
    token = token.strip("<>[]()\"'")
    if not token:
        return None
    if token.lower().startswith("file://"):
        return _file(token, Path(unquote(urlsplit(token).path)))
    if _SCHEME.match(token):
        return _url(token, allow_other_sites)
    if token.startswith(("/", "~", "./", "../")) or Path(token).suffix.lower() in VIDEO_EXTS:
        return _file(token, Path(token))
    if token.lower().startswith("www.") or _BARE_DOMAIN.match(token):
        return _url(token, allow_other_sites)
    return None


def _key(item: SourceRef | Rejected) -> tuple:
    if isinstance(item, Rejected):
        return ("rejected", item.token)
    return (item.kind, item.video_id or item.path or item.url)


def parse_inputs(text: str, *, allow_other_sites: bool = False) -> tuple[list[SourceRef], list[Rejected]]:
    """Every source in ``text``, in order and without repeats, plus what could not be used and why."""
    refs: list[SourceRef] = []
    rejected: list[Rejected] = []
    seen: set[tuple] = set()
    for token in _tokens(text):
        item = _classify(token, allow_other_sites)
        if item is None or _key(item) in seen:
            continue
        seen.add(_key(item))
        (refs if isinstance(item, SourceRef) else rejected).append(item)
    return refs, rejected
