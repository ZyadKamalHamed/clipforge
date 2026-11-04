"""YouTube through yt-dlp: metadata, audio, captions, comments, live chat and video (SPEC 7.1 and 7.2), and
Google Drive's shared files and folders the same way.

There is no cookie option anywhere in here. A sign-in wall becomes a plain refusal or "YouTube is
rate-limiting this Mac", never a cookie grab.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yt_dlp
from yt_dlp.extractor.googledrive import GoogleDriveFolderIE
from yt_dlp.utils import IDENTITY, DownloadError, download_range_func

from clipforge import children
from clipforge.asr.base import base_language
from clipforge.doctor import js_runtime
from clipforge.errors import ClipForgeError, NetworkError, SourceRefused, YouTubeRateLimited
from clipforge.ingest.urls import drive_file_id, drive_file_url, is_drive, video_title
from clipforge.models import Chapter, Comment, HeatBin, SourceMeta
from clipforge.untrusted import clean_text
from clipforge.watch.models import TITLE_MAX, ChannelListing

log = logging.getLogger("clipforge.ytdlp")

Progress = Callable[[float], None]

# "lang" first in every sort, so an auto-dubbed audio track is never picked over the original. SDR video
# first: YouTube's own SDR versions are tone mapped, while the renderer clips HDR highlights, so an HDR
# stream is taken only when there is no other.
QUALITY: dict[str, tuple[str, list[str]]] = {
    "best": (
        "bv*[height<=2160][dynamic_range=SDR]+ba[ext=m4a]/bv*[height<=2160]+ba[ext=m4a]/b",
        ["lang", "res:2160", "vcodec:av01", "acodec:aac"],
    ),
    "balanced": (
        "bv*[height<=1440][dynamic_range=SDR]+ba[ext=m4a]/bv*[height<=1440]+ba[ext=m4a]/b",
        ["lang", "res:1440", "vcodec:av01", "acodec:aac"],
    ),
    "fast": (
        "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]",
        ["lang", "res:1080", "vcodec:h264", "acodec:aac"],
    ),
}
_MAX_HEIGHT = {"best": 2160, "balanced": 1440, "fast": 1080}
DESCRIPTION_MAX = 5000

RATE_LIMITED = "YouTube is rate-limiting this Mac. Try again in an hour."
NETWORK = "Network trouble talking to YouTube. Check the internet connection and try again."
FAILED = "YouTube download failed. Try again; if it keeps failing, updating yt-dlp usually fixes it."
REFUSED = {
    "live": "This stream is still live. Try again when it has ended.",
    "processing": "This stream just ended and YouTube is still processing it. Try again in an hour.",
    "upcoming": "This video hasn't premiered yet.",
    "private": "This video is private.",
    "members": "This video is for channel members only.",
    "age": "YouTube wants a sign-in for this one. Download it yourself and drop the file in.",
    "unavailable": (
        "YouTube says this video is unavailable. It may have been removed or blocked in your country."
    ),
}
PREMIUM = "This video is only for YouTube Premium members."
NO_LENGTH = "YouTube didn't give this video's length, so ClipForge can't clip it. Try again later."
NO_CHANNEL = "YouTube didn't say which channel that is. Check the link and try again."
DRIVE_PRIVATE = (
    "This Drive file isn't shared as Anyone with the link. Share it that way, or ask whoever sent the link "
    "to, then add it again."
)
DRIVE_FOLDER_PRIVATE = (
    "This Drive folder isn't shared as Anyone with the link. Share it that way, or ask whoever sent the "
    "link to, then add it again."
)
DRIVE_QUOTA = (
    "Google has stopped downloads of this Drive file for now, as it does when many people fetch one file. "
    "Try again later; Google can take up to a day to lift it."
)
DRIVE_NETWORK = "Network trouble talking to Google Drive. Check the internet connection and try again."
DRIVE_FAILED = (
    "Google Drive download failed. Try again; if it keeps failing, download the file yourself and drop it in."
)
CHANNEL_GONE = "YouTube says this channel was removed or doesn't exist. Check its link."
# What yt-dlp says, lower case, when a channel's page is gone: YouTube's own alerts, which yt-dlp passes on
# as "YouTube said: ...", its own sentence when the link lands on the home page, and a handle no one has.
_GONE = (
    "account has been terminated",
    "channel was removed",
    "channel does not exist",
    "channel/playlist does not exist",
    "http error 404",
)
SECTIONS_TIMEOUT = "This clip's part of the video took too long to download. Try again."
STOPPED = "The download was stopped."

# A section download's time limit: 5 minutes, plus 10 s for each second of video, several times what a 4K
# section takes on a slow line and a busy Mac. Cutting on exact frames means ffmpeg encodes the section
# again: on 4K AV1, libx264's veryfast at quality 18 took half the time of its default and kept more of the
# picture; the sound keeps 192k, since the render encodes it once more.
SECTIONS_BASE_S, SECTIONS_PER_S = 300.0, 10.0
SECTION_ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]
POLL_S = 0.5  # how often a child process's job is checked for a cancel


# Options


def js_runtimes() -> dict[str, dict[str, str]]:
    """The JavaScript runtime yt-dlp solves YouTube's challenges with: the venv's deno, else deno or
    node on PATH (the same search as doctor), else none."""
    found = js_runtime()
    if found is None:
        return {}
    return {"deno" if found.name.startswith("deno") else "node": {"path": str(found)}}


def _backoff(n: int) -> float:
    return float(min(2**n, 30))


class _Logger:
    """yt-dlp's output, sent through logging so the handlers mask anything secret in it."""

    def debug(self, msg: str) -> None:
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.info(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        log.error(msg)


def base_opts() -> dict[str, Any]:
    """What every yt-dlp call starts from. Deliberately no cookies and no cookie file."""
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "js_runtimes": js_runtimes(),
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "retry_sleep_functions": {"http": _backoff, "fragment": _backoff, "extractor": _backoff},
        "socket_timeout": 30,
        "logger": _Logger(),
    }


# Reading the info dict


_DROP = {"http_headers", "cookies"}


def clean_info(info: dict) -> dict:
    """The info dict as plain JSON: no yt-dlp private keys, no requested_* keys, no headers or cookies."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in _DROP}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    return strip(yt_dlp.YoutubeDL.sanitize_info(info, remove_private_keys=True))


def pick_caption_track(info: dict) -> str | None:
    """The automatic track YouTube made from the audio itself (``en-orig`` style) in the video's language,
    else a manual track in it, else automatic English for an English video with neither. A dubbed video
    has one such original track for each dub, so with the language known another language's track is
    never taken, nor a translation of one; with it unknown, an original track is taken only when there
    is just one."""
    auto = info.get("automatic_captions") or {}
    manual = info.get("subtitles") or {}
    language = info.get("language")
    origs = [key for key in auto if key.endswith("-orig")]
    if base_language(language) is None:
        if origs:
            return origs[0] if len(origs) == 1 else None  # dubbed, and nothing says which is the original
        return "en" if "en" in auto else None
    track = _in_language(origs, language, "-orig") or _in_language(list(manual), language)
    if track or origs or base_language(language) != "en":
        return track
    return "en" if "en" in auto else None


def _in_language(keys: list[str], language: str, suffix: str = "") -> str | None:
    """The key for ``language`` (each key ending in ``suffix``): its own code, else the code without its
    region, else the first other one for the same language ("es-US" for "es")."""
    base = base_language(language)
    same = [key for key in keys if key.endswith(suffix) and base_language(key.removesuffix(suffix)) == base]
    exact = next((key for key in (f"{language}{suffix}", f"{base}{suffix}") if key in same), None)
    return exact or next(iter(same), None)


_CODEC_RANK = (("av01", 3), ("vp09", 2), ("vp9", 2), ("avc1", 1))


def _codec_rank(vcodec: str) -> int:
    return next((rank for prefix, rank in _CODEC_RANK if vcodec.startswith(prefix)), 0)


def estimate_video_bytes(info: dict, quality: str) -> int | None:
    """Roughly what the preset's video stream weighs. Streams are ranked the way the preset's yt-dlp
    sort ranks them (SDR before HDR, then height, frame rate, AV1 over VP9 over H.264, then a known size
    over an HLS peak bitrate), so this is the stream that will download, not just the biggest one."""
    limit = _MAX_HEIGHT[quality]
    streams = [
        f
        for f in info.get("formats") or []
        if f.get("vcodec") not in (None, "none")
        and f.get("height")
        and f["height"] <= limit
        and (quality != "fast" or f["vcodec"].startswith("avc1"))
    ]
    if not streams:
        return None
    streams = [f for f in streams if (f.get("dynamic_range") or "SDR") == "SDR"] or streams

    def rank(f: dict) -> tuple:
        known = bool(f.get("filesize") or f.get("filesize_approx"))
        return (f["height"], f.get("fps") or 0, _codec_rank(f["vcodec"]), known, f.get("tbr") or 0)

    best = max(streams, key=rank)
    size = best.get("filesize") or best.get("filesize_approx")
    if size:
        return int(size)
    if best.get("tbr") and info.get("duration"):
        return round(best["tbr"] * 1000 / 8 * info["duration"])
    return None


def meta_from_info(info: dict) -> SourceMeta:
    license_ = info.get("license")
    return SourceMeta(
        source_kind="youtube",
        video_id=info.get("id"),
        title=info.get("title") or info.get("id") or "Untitled",
        channel=info.get("channel") or info.get("uploader"),
        channel_url=info.get("channel_url") or info.get("uploader_url"),
        duration_s=float(info.get("duration") or 0),
        upload_date=info.get("upload_date"),
        description=(info.get("description") or "")[:DESCRIPTION_MAX],
        language=info.get("language"),
        license=license_,
        is_creative_commons="Creative Commons" in (license_ or ""),
        chapters=[
            Chapter(start=c["start_time"], end=c["end_time"], title=c.get("title") or "")
            for c in info.get("chapters") or []
        ],
        heatmap=[
            HeatBin(start=h["start_time"], end=h["end_time"], value=h["value"])
            for h in info.get("heatmap") or []
        ],
        live_status=info.get("live_status"),
        availability=info.get("availability"),
        age_limit=int(info.get("age_limit") or 0),
        view_count=info.get("view_count"),
        thumbnail_url=info.get("thumbnail"),
        caption_track=pick_caption_track(info),
        video_bytes={quality: estimate_video_bytes(info, quality) for quality in QUALITY},
    )


# Refusals and errors


_LIVE = {"is_live": "live", "post_live": "processing", "is_upcoming": "upcoming"}


def refusal_for(meta: SourceMeta, *, max_hours: float = 6.0) -> SourceRefused | None:
    """Why this source can't be clipped, in a plain sentence, or None when it can."""
    live = _LIVE.get(meta.live_status or "")
    if live:
        return SourceRefused(live, REFUSED[live])
    if meta.availability == "private":
        return SourceRefused("private", REFUSED["private"])
    if meta.availability == "subscriber_only":
        return SourceRefused("members", REFUSED["members"])
    if meta.availability == "premium_only":
        return SourceRefused("members", PREMIUM)
    if meta.availability == "needs_auth" or meta.age_limit >= 18:
        return SourceRefused("age", REFUSED["age"])
    if not meta.duration_s:
        return SourceRefused("unavailable", NO_LENGTH)
    if meta.duration_s > max_hours * 3600:
        if meta.source_kind == "file":
            advice = "Trim it to the part you want and drop that in."
        else:
            advice = "Download the part you want and drop the file in."
        return SourceRefused("too_long", f"This video is over {max_hours:g} hours. {advice}")
    return None


# Lower case, with curly apostrophes made straight before matching. The first matching row wins.
_ERRORS: list[tuple[str, tuple[str, ...]]] = [
    (
        "rate_limited",
        (
            "confirm you're not a bot",
            "http error 429",
            "rate-limited by youtube",
            "content isn't available, try again later",
            "captcha",
        ),
    ),
    ("age", ("sign in to confirm your age", "age-restricted", "inappropriate for some users")),
    ("private", ("private video", "this video is private")),
    ("members", ("members-only", "join this channel", "channel's members")),
    ("upcoming", ("premieres in", "this live event will begin")),
    (
        "unavailable",
        ("video unavailable", "video is unavailable", "has been removed", "available in your country"),
    ),
    (
        "network",
        (
            "timed out",
            "connection reset",
            "connection aborted",
            "connection refused",
            "remote end closed connection",
            "incompleteread",
            "temporary failure in name resolution",
            "failed to resolve",  # macOS: [Errno 8] nodename nor servname provided
            "nodename nor servname",
            "network is unreachable",
            "http error 5",
        ),
    ),
]


def classify_error(message: str) -> ClipForgeError:
    """yt-dlp's error text as a ClipForge error with a plain message; the text itself goes in details."""
    text = message.lower().replace(chr(0x2019), "'")
    code = next((code for code, needles in _ERRORS if any(n in text for n in needles)), None)
    if code == "rate_limited":
        return YouTubeRateLimited(code, RATE_LIMITED, details=message)
    if code == "network":
        return NetworkError(code, NETWORK, details=message)
    if code:
        return SourceRefused(code, REFUSED[code], details=message)
    return ClipForgeError("ytdlp_failed", FAILED, details=message)


# Drive's errors, lower case like the table above. Drive answers 401, 403 or 404 for a file that isn't
# shared with everyone, and a folder that isn't comes back as a page with no key to list it with.
_DRIVE_QUOTA = ("too many users have viewed or downloaded", "quota", "http error 429")
_DRIVE_PRIVATE = (
    "http error 401",
    "http error 403",
    "http error 404",
    "permission",
    "access denied",
    "sign in",
)
_FOLDER_PRIVATE = (*_DRIVE_PRIVATE, "http error 4", "unable to extract key")


def drive_error(message: str, *, folder: bool = False) -> ClipForgeError:
    """yt-dlp's error text for a Drive link, in Drive's terms: share it, or wait out Google's limit."""
    text = message.lower().replace(chr(0x2019), "'")
    if any(n in text for n in _DRIVE_QUOTA):
        return ClipForgeError("drive_quota", DRIVE_QUOTA, details=message)
    if any(n in text for n in (_FOLDER_PRIVATE if folder else _DRIVE_PRIVATE)):
        return SourceRefused("private", DRIVE_FOLDER_PRIVATE if folder else DRIVE_PRIVATE, details=message)
    if any(n in text for n in dict(_ERRORS)["network"]):
        return NetworkError("network", DRIVE_NETWORK, details=message)
    return ClipForgeError("ytdlp_failed", DRIVE_FAILED, details=message)


def error_for(url: str, message: str, *, folder: bool = False) -> ClipForgeError:
    return drive_error(message, folder=folder) if is_drive(url) else classify_error(message)


# Progress


def progress_hook(progress: Progress) -> Callable[[dict], None]:
    """A yt-dlp progress hook that reports a fraction which only rises, in steps of at least 1 percent.
    A video and its audio download one after the other, and the second file must not send the bar
    back to zero."""
    shown = 0.0

    def hook(status: dict) -> None:
        nonlocal shown
        if status.get("status") == "finished":
            fraction = 1.0
        elif status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            if not total:
                return
            fraction = min(1.0, (status.get("downloaded_bytes") or 0) / total)
        else:
            return
        if fraction >= shown + 0.01 or (fraction == 1.0 and shown < 1.0):
            shown = fraction
            progress(fraction)

    return hook


# The client


@dataclass(frozen=True)
class DriveFile:
    id: str
    title: str  # the file's name without its video extension, "" when Drive gave none


@dataclass(frozen=True)
class DriveFolder:
    """A Drive folder's first videos, as many as were asked for, and whether it has more."""

    title: str
    files: list[DriveFile]
    more: bool


class DriveFolderIE(GoogleDriveFolderIE):
    """yt-dlp's Drive folder listing, keeping each item's id and name and leaving out what isn't a video:
    yt-dlp's own lists every item, folders and documents too, as bare links. This leans on yt-dlp handing
    playlist_from_matches the folder's items; were that to change, its plain links still come through."""

    @classmethod
    def playlist_from_matches(
        cls,
        matches,
        playlist_id=None,
        playlist_title=None,
        getter=IDENTITY,
        ie=None,
        video_kwargs=None,
        **kwargs,
    ):
        def entries():
            for item in matches:  # lazily, so a listing cut short fetches no more pages than it needs
                if not isinstance(item, dict):
                    yield cls.url_result(item if isinstance(item, str) else getter(item), ie)
                elif str(item.get("mimeType") or "").startswith("video/") and item.get("id"):
                    yield cls.url_result(drive_file_url(item["id"]), ie, item["id"], item.get("title"))

        return cls.playlist_result(entries(), playlist_id, playlist_title, **kwargs)


class YouTubeClient(Protocol):
    def fetch_info(self, url: str) -> dict: ...

    def list_channel(self, url: str, limit: int) -> ChannelListing: ...

    def list_drive_folder(self, url: str, limit: int) -> DriveFolder: ...

    def download_audio(self, url: str, dest_dir: Path, *, progress: Progress | None = None) -> Path: ...

    def download_captions(self, url: str, dest_dir: Path, track: str) -> Path | None: ...

    def fetch_comments(self, url: str, max_comments: int = 300) -> list[Comment]: ...

    def download_live_chat(self, url: str, dest_dir: Path, *, timeout_s: float = 600.0) -> Path | None: ...

    def download_video(
        self, url: str, dest_dir: Path, quality: str, *, progress: Progress | None = None
    ) -> Path: ...

    def download_sections(
        self,
        url: str,
        dest_dir: Path,
        ranges: list[tuple[float, float]],
        quality: str,
        *,
        cancelled: Callable[[], bool] = ...,
    ) -> list[Path]: ...


def _files(dest_dir: Path, template: str) -> dict[str, Any]:
    # The folder goes in "paths", so a % in it is never read as part of the template.
    return {"paths": {"home": str(Path(dest_dir).absolute())}, "outtmpl": {"default": template}}


def _hooks(progress: Progress | None) -> dict[str, Any]:
    return {"progress_hooks": [progress_hook(progress)]} if progress else {}


def _downloaded(info: dict) -> list[Path]:
    paths = [Path(d["filepath"]) for d in info.get("requested_downloads") or [] if d.get("filepath")]
    if not paths:
        raise ClipForgeError(
            "ytdlp_failed", FAILED, details="yt-dlp finished without naming the file it wrote"
        )
    return paths


def live_chat_command(url: str, dest_dir: Path) -> list[str]:
    """yt-dlp's command line for a chat replay. --ignore-config keeps a personal yt-dlp config, and any
    cookie option in it, out of the run."""
    runtimes = [
        arg for name, cfg in js_runtimes().items() for arg in ("--js-runtimes", f"{name}:{cfg['path']}")
    ]
    return [
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--quiet", "--no-progress",
        "--skip-download", "--write-subs", "--sub-langs", "live_chat", "--no-playlist", *runtimes,
        "-P", str(Path(dest_dir).absolute()), "-o", "chat", "--", url,
    ]  # fmt: skip


def sections_command(url: str, dest_dir: Path, ranges: list[tuple[float, float]], quality: str) -> list[str]:
    """The section download's child process: this Python, isolated so nothing in the environment or the
    folder it starts in is imported, and told ClipForge's process id, so it stops once ClipForge is gone."""
    edges = [repr(float(x)) for pair in ranges for x in pair]
    return [
        sys.executable, "-I", "-m", "clipforge.ingest.section_child", str(os.getpid()),
        url, str(dest_dir), quality, *edges,
    ]  # fmt: skip


def _never() -> bool:
    return False


def _supervise(
    cmd: list[str], limit_s: float, late: ClipForgeError, cancelled: Callable[[], bool] = _never
) -> tuple[int, bytes, bytes]:
    """Runs ``cmd`` in a process group of its own and gives back its exit code, output and error output.
    The group is killed, and ``late`` raised, at ``limit_s``; killed once ``cancelled()``; and killed
    whatever else ends the wait, so nothing it started outlives it."""
    proc = children.popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # its own process group, so the kill reaches ffmpeg and deno too
    )
    deadline = time.monotonic() + limit_s
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=max(0.0, min(POLL_S, deadline - time.monotonic())))
                return proc.returncode, out, err
            except subprocess.TimeoutExpired:
                if cancelled():
                    raise ClipForgeError("cancelled", STOPPED) from None
                if time.monotonic() >= deadline:
                    raise late from None
    except BaseException:
        with contextlib.suppress(ProcessLookupError, PermissionError):  # it may have just ended
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise


def _tail(stderr: bytes) -> str:
    return "\n".join(stderr.decode("utf-8", "replace").strip().splitlines()[-20:])


def _reply(stdout: bytes) -> dict[str, Any]:
    """The section child's answer: the JSON on its last line of output, or nothing."""
    lines = stdout.decode("utf-8", "replace").strip().splitlines()
    with contextlib.suppress(ValueError):
        reply = json.loads(lines[-1]) if lines else None
        if isinstance(reply, dict):
            return reply
    return {}


_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{28,}")
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")


def _title(text: object) -> str:
    return (clean_text(text) if isinstance(text, str) else "").strip()[:TITLE_MAX]


class YtDlpClient:
    """The real YouTubeClient. ``ydl_class`` is yt_dlp.YoutubeDL; tests hand in a stand-in."""

    def __init__(self, ydl_class: Callable[[dict], Any] = yt_dlp.YoutubeDL):
        self._ydl_class = ydl_class

    def _extract(self, url: str, extra: dict[str, Any], *, download: bool, ie: type | None = None) -> dict:
        """yt-dlp's answer for ``url``, from extractor ``ie`` alone when one is given."""
        try:
            with self._ydl_class({**base_opts(), **extra}) as ydl:
                if ie is None:
                    return ydl.extract_info(url, download=download) or {}
                ydl.add_info_extractor(ie())
                return ydl.extract_info(url, download=download, ie_key=ie.ie_key()) or {}
        except DownloadError as err:
            raise error_for(url, str(err), folder=ie is DriveFolderIE) from err

    def fetch_info(self, url: str) -> dict:
        # The heatmap comes from the watch page, so the page is never skipped (no player_skip).
        return clean_info(self._extract(url, {}, download=False))

    def list_channel(self, url: str, limit: int) -> ChannelListing:
        """A channel's newest ``limit`` uploads from one flat listing of its videos tab: ids and titles
        only, no video pages (SPEC 10.9)."""
        extra = {"extract_flat": "in_playlist", "playlistend": limit, "noplaylist": False}
        try:
            info = self._extract(url, extra, download=False)
        except ClipForgeError as err:
            said = (err.details or "").lower()
            if err.code in ("ytdlp_failed", "unavailable") and any(n in said for n in _GONE):
                raise SourceRefused("channel_gone", CHANNEL_GONE, details=err.details) from err
            raise
        channel_id = info.get("channel_id") or info.get("id") or ""
        if not _CHANNEL_ID.fullmatch(channel_id):
            raise ClipForgeError("unavailable", NO_CHANNEL, details=f"listing id {channel_id[:40]!r}")
        entries = []
        for entry in info.get("entries") or []:
            video_id = (entry or {}).get("id") or ""
            if _VIDEO_ID.fullmatch(video_id):
                entries.append((video_id, _title(entry.get("title"))))
        return ChannelListing(
            channel_id=channel_id,
            title=_title(info.get("channel") or info.get("uploader") or info.get("title")),
            url=f"https://www.youtube.com/channel/{channel_id}/videos",
            entries=entries[:limit],
        )

    def list_drive_folder(self, url: str, limit: int) -> DriveFolder:
        """A shared Drive folder's first ``limit`` videos from one flat listing: ids and names, nothing
        downloaded. Only the videos directly in it count; folders inside it are not opened."""
        extra = {"extract_flat": "in_playlist", "playlistend": limit + 1, "noplaylist": False}
        info = self._extract(url, extra, download=False, ie=DriveFolderIE)
        files = []
        for entry in info.get("entries") or []:
            entry = entry or {}
            file_id = entry.get("id") or drive_file_id(entry.get("url") or "") or ""
            if _DRIVE_ID.fullmatch(file_id):
                files.append(DriveFile(file_id, video_title(_title(entry.get("title")))))
        return DriveFolder(_title(info.get("title")), files[:limit], more=len(files) > limit)

    def download_audio(self, url: str, dest_dir: Path, *, progress: Progress | None = None) -> Path:
        extra = {"format": "ba[ext=m4a]/ba/b", "format_sort": ["lang"], **_files(dest_dir, "audio.%(ext)s")}
        return _downloaded(self._extract(url, {**extra, **_hooks(progress)}, download=True))[0]

    def download_captions(self, url: str, dest_dir: Path, track: str) -> Path | None:
        extra = {
            "skip_download": True,
            "writeautomaticsub": True,
            "writesubtitles": True,
            "subtitleslangs": [track],
            "subtitlesformat": "json3",
            "sleep_interval_subtitles": 5,
            **_files(dest_dir, "subs.%(ext)s"),
        }
        self._extract(url, extra, download=True)
        path = Path(dest_dir).absolute() / f"subs.{track}.json3"
        return path if path.exists() else None

    def fetch_comments(self, url: str, max_comments: int = 300) -> list[Comment]:
        cap = str(max_comments)
        args = {"youtube": {"comment_sort": ["top"], "max_comments": [cap, cap, "0"]}}
        info = self._extract(url, {"getcomments": True, "extractor_args": args}, download=False)
        comments = [
            Comment(text=c["text"], likes=int(c.get("like_count") or 0))
            for c in info.get("comments") or []
            if c.get("text") and c.get("parent", "root") == "root"
        ]
        return comments[:max_comments]

    def download_live_chat(self, url: str, dest_dir: Path, *, timeout_s: float = 600.0) -> Path | None:
        """The chat replay of a finished stream, or None when it has none. It runs in a child process
        because a chat download can't be interrupted inside this one."""
        dest_dir = Path(dest_dir).absolute()
        dest_dir.mkdir(parents=True, exist_ok=True)
        late = ClipForgeError(
            "chat_timeout",
            "The live chat replay took too long to download, so ClipForge skipped it.",
            details=f"stopped after {timeout_s} s",
        )
        try:
            code, _, stderr = _supervise(live_chat_command(url, dest_dir), timeout_s, late)
        except BaseException:
            for part in dest_dir.glob("chat.*.part"):
                part.unlink(missing_ok=True)
            raise
        if code != 0:
            raise classify_error(_tail(stderr) or f"yt-dlp exited with code {code}")
        path = dest_dir / "chat.live_chat.json"
        return path if path.exists() else None

    def download_video(
        self, url: str, dest_dir: Path, quality: str, *, progress: Progress | None = None
    ) -> Path:
        fmt, sort = QUALITY[quality]
        if is_drive(url):  # the file as uploaded, whatever the setting; Google's copies when it's stopped
            fmt = f"source/{fmt}"
        extra = {"format": fmt, "format_sort": list(sort), "merge_output_format": "mp4"}
        extra |= _files(dest_dir, "video.%(ext)s") | _hooks(progress)
        return _downloaded(self._extract(url, extra, download=True))[0]

    def download_sections(
        self,
        url: str,
        dest_dir: Path,
        ranges: list[tuple[float, float]],
        quality: str,
        *,
        cancelled: Callable[[], bool] = _never,
    ) -> list[Path]:
        """One file per range, cut on exact frames, in the order given. yt-dlp runs in a child process that
        is stopped once ``cancelled()`` or at its time limit, and each attempt writes in a hidden folder of
        its own, so two attempts never write the same file and a stopped one leaves nothing behind."""
        if not ranges:
            return []
        dest_dir = Path(dest_dir).absolute()
        limit = SECTIONS_BASE_S + SECTIONS_PER_S * sum(b - a for a, b in ranges)
        late = ClipForgeError("sections_timeout", SECTIONS_TIMEOUT, details=f"stopped after {limit:.0f} s")
        work = Path(tempfile.mkdtemp(prefix=".sections-", dir=dest_dir))
        try:
            cmd = sections_command(url, work, ranges, quality)
            code, stdout, stderr = _supervise(cmd, limit, late, cancelled)
            reply = _reply(stdout)
            if "error" in reply:
                raise error_for(url, str(reply["error"]))
            if code != 0 or "paths" not in reply:
                raise error_for(url, _tail(stderr) or f"the section download exited with code {code}")
            done = [dest_dir / Path(p).name for p in reply["paths"]]
            for path in done:
                os.replace(work / path.name, path)
            return done
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def sections_here(
        self, url: str, dest_dir: Path, ranges: list[tuple[float, float]], quality: str
    ) -> list[Path]:
        """What the section child runs: download_sections in this process."""
        if not ranges:
            return []  # yt-dlp would read an empty range list as the whole video
        fmt, sort = QUALITY[quality]
        extra = {
            "format": fmt,
            "format_sort": list(sort),
            "merge_output_format": "mp4",
            "download_ranges": download_range_func([], [(float(a), float(b)) for a, b in ranges]),
            "force_keyframes_at_cuts": True,
            "external_downloader_args": {"ffmpeg_o": list(SECTION_ENCODE)},
            # Ranges share a video id, so their start and end keep the file names apart.
            **_files(dest_dir, "video.%(section_start)s-%(section_end)s.%(ext)s"),
        }
        return _downloaded(self._extract(url, extra, download=True))
