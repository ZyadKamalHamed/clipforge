"""Public media for Instagram Login (SPEC 10.2 Mode B, 17). Meta fetches a Mode B Reel from a URL, so a
post's video is given a random address under the public base URL while the post is with Instagram, and
the public host answers nothing else. The addresses live in memory only: a restart drops them all."""

from __future__ import annotations

import re
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from clipforge.config import PostingSettings

if TYPE_CHECKING:
    from clipforge.app import Engine

TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")  # 32 random bytes, URL-safe base64
NAME = "clip.mp4"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
FIRST = 1024  # the bytes the self-test reads, as Meta's first range read would
UNREACHABLE = (
    "Instagram can't reach this Mac. Check that Tailscale Funnel is on for {host}, then press Retry."
)


class PublicMedia:
    """The live addresses: one per post at most, each a token to the video's path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paths: dict[str, tuple[str, Path]] = {}  # token to (post id, video)
        self._tokens: dict[str, str] = {}  # post id to its token

    def issue(self, post_id: str, path: Path) -> str:
        """A new address for the post's video, which ends the post's old one."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            old = self._tokens.pop(post_id, None)
            if old is not None:
                self._paths.pop(old, None)
            self._paths[token] = (post_id, Path(path))
            self._tokens[post_id] = token
        return token

    def revoke(self, post_id: str) -> None:
        with self._lock:
            token = self._tokens.pop(post_id, None)
            if token is not None:
                self._paths.pop(token, None)

    def lookup(self, token: str) -> Path | None:
        with self._lock:
            found = self._paths.get(token)
        return found[1] if found is not None else None

    def live(self, post_id: str) -> bool:
        with self._lock:
            return post_id in self._tokens


def public_host(settings: PostingSettings) -> str | None:
    """The public base URL's host, lowercased, or None when there is no public address."""
    base = settings.public_base_url
    return urlsplit(base).netloc.lower() if base else None


def public_url(settings: PostingSettings, token: str) -> str:
    return f"{(settings.public_base_url or '').rstrip('/')}/m/{token}/{NAME}"


def token_in(path: str, settings: PostingSettings) -> str | None:
    """The token in a public request's path: ``/m/<token>/clip.mp4``, under the base URL's own path or
    not, since a Funnel mount path may be passed on or taken off."""
    prefix = urlsplit(settings.public_base_url or "").path.rstrip("/")
    for start in {"", prefix}:
        rest = path[len(start) :] if path.startswith(start + "/m/") else None
        if rest is None:
            continue
        parts = rest.split("/")
        if len(parts) == 4 and parts[3] == NAME and TOKEN.fullmatch(parts[2]):
            return parts[2]
    return None


def file_for(engine: Engine, path: str) -> Path | None:
    """The video a public request's path names, while its address is live and the file is there."""
    token = token_in(path, engine.settings.posting)
    file = engine.public_media.lookup(token) if token else None
    return file if file is not None and file.is_file() else None


def self_test(url: str, size: int, *, transport: httpx.BaseTransport | None = None) -> str | None:
    """Fetches ``url`` as Meta would, a HEAD and then the first 1024 bytes: None when both look right, else
    what was wrong. The URL holds the token, so it never appears in the answer."""
    want = min(size, FIRST)
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT, follow_redirects=False) as client:
            head = client.head(url)
            if head.status_code != 200:
                return f"HEAD answered {head.status_code}."
            kind = head.headers.get("content-type", "")
            if kind.split(";")[0].strip() != "video/mp4":
                return f"HEAD gave the type {kind or 'none'}, not video/mp4."
            length = head.headers.get("content-length")
            if length != str(size):
                return f"HEAD gave the size {length or 'none'}, not {size} bytes."
            part = client.get(url, headers={"range": f"bytes=0-{FIRST - 1}"})
            if part.status_code != 206:
                return f"A range read answered {part.status_code}, not 206."
            if len(part.content) != want:
                return f"A range read gave {len(part.content)} bytes, not {want}."
    except httpx.HTTPError as err:
        return f"The fetch failed ({type(err).__name__})."
    return None
