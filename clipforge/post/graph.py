"""Meta's Graph API as ClipForge uses it (SPEC 10.2): the calls behind linking and posting to Instagram,
with the token always in an ``Authorization`` header and never in a URL, and Meta's errors read into
what they mean for a post."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field

from clipforge.untrusted import clean_text

GRAPH_FB = "https://graph.facebook.com"  # Mode A, and where the upload's container lives
GRAPH_IG = "https://graph.instagram.com"  # Mode B
UPLOAD_HOST = "rupload.facebook.com"  # the only host a video's bytes go to
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
UPLOAD_TIMEOUT = httpx.Timeout(600.0, connect=10.0)
CHUNK = 1 << 20
PAGES_MAX = 20  # pages of /me/accounts followed at most
MESSAGE_MAX = 300
DEFAULT_QUOTA = 50  # Meta's posts per 24 hours, when the limit doesn't say
PAGE_FIELDS = "id,name,access_token,instagram_business_account{id,username,profile_picture_url}"
ME_FIELDS = "user_id,username,account_type,profile_picture_url"
STATUS_FIELDS = "status_code,status,copyright_check_status"
MEDIA_FIELDS = "permalink,timestamp,media_product_type"
RECENT_FIELDS = "id,caption,timestamp,permalink"
INSIGHT_METRICS = ("views", "likes", "comments", "shares", "saved")  # a reel's lifetime counts

Action = Literal[
    "poll",
    "later_24h",
    "pause_fatal",
    "open_app",
    "new_container",
    "retest_url",
    "reconnect",
    "rate_limited",
    "retry",
    "fatal",
]
SUBCODES: dict[int, Action] = {  # Meta's publishing errors table, by subcode
    2207027: "poll",
    2207042: "later_24h",
    2207051: "pause_fatal",
    2207050: "open_app",
    2207008: "new_container",
    2207020: "new_container",
    2207053: "new_container",
    2207052: "retest_url",
    2207003: "retest_url",
}
RATE_LIMITED = {4, 17, 32, 613}
TRANSIENT = {1, 2}  # Meta's unknown error and service unavailable, when no subcode says more


class GraphError(Exception):
    """An answer from Meta that isn't what was asked for. ``status`` is 0 when no answer came; ``code`` is
    None when the answer wasn't Meta's JSON. ``message`` never holds a token."""

    def __init__(self, status: int, code: int | None, subcode: int | None, message: str):
        self.status = status
        self.code = code
        self.subcode = subcode
        self.message = message
        super().__init__(str(self))

    def __str__(self) -> str:
        code = "?" if self.code is None else str(self.code)
        if self.subcode is not None:
            code += f"/{self.subcode}"
        return f"Graph error {code} (HTTP {self.status}): {self.message}"


def action_for(err: GraphError) -> Action:
    """What ClipForge does about ``err``: the subcode first, then the code."""
    if err.subcode in SUBCODES:
        return SUBCODES[err.subcode]
    code = err.code
    if code == 9007:
        return "poll"
    if code in (190, 10) or (code is not None and 200 <= code < 300):  # the token, or a permission
        return "reconnect"
    if code in RATE_LIMITED:
        return "rate_limited"
    if code is None or err.status == 0 or err.status >= 500 or (code in TRANSIENT and err.subcode is None):
        return "retry"
    return "fatal"


class PageCandidate(BaseModel):
    """An Instagram account linked to one of the person's Facebook Pages, offered for linking."""

    page_id: str
    page_name: str
    ig_user_id: str
    username: str | None = None
    picture_url: str | None = None
    page_token: str = Field(exclude=True, repr=False)


class IgMe(BaseModel):
    user_id: str
    username: str | None = None
    account_type: str | None = None
    picture_url: str | None = None


class Container(BaseModel):
    id: str
    uri: str | None = None  # where the bytes go, for a resumable upload


class IgAudio(BaseModel):
    """One sound in Instagram's trending list, only the fields ClipForge asks for: never the sound's file,
    artwork or the artist's picture."""

    audio_id: str
    title: str
    display_artist: str | None = None
    ig_username: str | None = None
    audio_type: str
    duration_in_ms: int | None = None
    on_platform_audio_preview_link: str | None = None


IG_AUDIO_FIELDS = ",".join(IgAudio.model_fields)


class ContainerStatus(BaseModel):
    status_code: str
    status: str | None = None
    copyright: dict[str, Any] = {}


def _scrub(text: str, secrets: Iterable[str | None]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[hidden]")
    text = re.sub(r"(access_token=|client_secret=)[^&\s\"']+", r"\1[hidden]", text)
    return (clean_text(text) or "")[:MESSAGE_MAX]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _form(fields: dict[str, Any]) -> dict[str, str]:
    """Form fields as Meta reads them: booleans as true or false, Nones left out."""
    out = {}
    for key, value in fields.items():
        if value is None:
            continue
        out[key] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return out


def _next_page(link: Any) -> tuple[str | None, dict[str, str]]:
    """``paging.next`` as a URL and its query, the token Meta puts in it left out. None when it isn't an
    https link on graph.facebook.com."""
    if not isinstance(link, str):
        return None, {}
    parts = urlsplit(link)
    if parts.scheme != "https" or parts.hostname != "graph.facebook.com" or parts.port is not None:
        return None, {}
    query = {k: v for k, v in parse_qsl(parts.query) if k != "access_token"}
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")), query


def _chunks(path: Path, offset: int) -> Iterator[bytes]:
    with path.open("rb") as f:
        f.seek(offset)
        while chunk := f.read(CHUNK):
            yield chunk


def _required(body: dict[str, Any], key: str) -> str:
    """``body[key]`` as text. A success without it is garbled, so it counts as no answer at all."""
    value = body.get(key)
    if value is None or value == "":
        raise GraphError(200, None, None, f"Meta's answer had no {key}.")
    return str(value)


def _issues(value: Any) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else [json.dumps(value)]


class GraphClient:
    def __init__(self, version: str, *, transport: httpx.BaseTransport | None = None):
        self.version = version
        self._http = httpx.Client(
            transport=transport, timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": "ClipForge"}
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _url(self, host: str, *path: str) -> str:
        return "/".join((host, self.version, *path))

    def _request(
        self,
        method: str,
        url: str,
        token: str | None,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        content: Iterator[bytes] | None = None,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout = TIMEOUT,
        secrets: Iterable[str | None] = (),
    ) -> dict[str, Any]:
        secrets = (token, *secrets)
        sent = dict(headers or {})
        if token is not None:
            scheme = "Bearer" if urlsplit(url).hostname == "graph.instagram.com" else "OAuth"
            sent["Authorization"] = f"{scheme} {token}"
        try:
            r = self._http.request(
                method, url, params=params, data=data, content=content, headers=sent, timeout=timeout
            )
        except httpx.HTTPError as exc:
            raise GraphError(0, None, None, _scrub(f"{type(exc).__name__}: {exc}", secrets)) from None
        try:
            body = r.json()
        except ValueError:
            raise GraphError(
                r.status_code, None, None, _scrub(f"Not JSON: {r.text[:200]}", secrets)
            ) from None
        if not isinstance(body, dict):
            raise GraphError(r.status_code, None, None, "Not a JSON object")
        err = body.get("error")
        if err is not None or r.status_code >= 400:
            err = err if isinstance(err, dict) else {}
            message = err.get("error_user_msg") or err.get("message") or f"HTTP {r.status_code}"
            code, subcode = _int(err.get("code")), _int(err.get("error_subcode"))
            raise GraphError(r.status_code, code, subcode, _scrub(str(message), secrets))
        return body

    # Linking

    def pages(self, user_token: str) -> list[PageCandidate]:
        """Every Instagram account on the person's Facebook Pages, with each Page's token."""
        url: str | None = self._url(GRAPH_FB, "me", "accounts")
        params: dict[str, str] = {"fields": PAGE_FIELDS}
        found: list[PageCandidate] = []
        for _ in range(PAGES_MAX):
            if url is None:
                break
            body = self._request("GET", url, user_token, params=params)
            for page in body.get("data") or []:
                ig = page.get("instagram_business_account") or {}
                if not (ig.get("id") and page.get("access_token") and page.get("id")):
                    continue
                found.append(
                    PageCandidate(
                        page_id=str(page["id"]),
                        page_name=clean_text(str(page.get("name") or "")) or "",
                        ig_user_id=str(ig["id"]),
                        username=clean_text(ig.get("username")),
                        picture_url=ig.get("profile_picture_url"),
                        page_token=page["access_token"],
                    )
                )
            url, params = _next_page((body.get("paging") or {}).get("next"))
        return found

    def exchange(self, short_token: str, app_id: str, app_secret: str) -> str:
        """A long-lived user token for a short-lived one; the secret goes in the body, never the URL."""
        data = {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        }
        url = self._url(GRAPH_FB, "oauth", "access_token")
        body = self._request("POST", url, None, data=data, secrets=(short_token, app_secret))
        return _required(body, "access_token")

    def ig_me(self, token: str) -> IgMe:
        body = self._request("GET", self._url(GRAPH_IG, "me"), token, params={"fields": ME_FIELDS})
        return IgMe(
            user_id=str(body.get("user_id") or body.get("id")),
            username=clean_text(body.get("username")),
            account_type=body.get("account_type"),
            picture_url=body.get("profile_picture_url"),
        )

    def ig_refresh(self, token: str) -> tuple[str, int]:
        """A Mode B token refreshed: the new token and how many seconds it lasts."""
        url = f"{GRAPH_IG}/refresh_access_token"
        body = self._request("GET", url, token, params={"grant_type": "ig_refresh_token"})
        return _required(body, "access_token"), _int(body.get("expires_in")) or 0

    def ig_audio(
        self, host: str, ig_id: str, token: str, audio_type: Literal["music", "original_sound"]
    ) -> list[IgAudio]:
        """What's trending on Instagram for the account, in Meta's order: rows with no id are skipped, and
        Meta's other fields are dropped unread."""
        params = {"audio_type": audio_type, "user_id": ig_id, "fields": IG_AUDIO_FIELDS}
        body = self._request("GET", self._url(host, "ig_audio"), token, params=params)
        rows = body.get("data") or []
        if not isinstance(rows, list):
            raise GraphError(200, None, None, "The list of sounds wasn't a list")
        found: list[IgAudio] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            audio_id = row.get("audio_id")
            if isinstance(audio_id, bool) or not isinstance(audio_id, (str, int)) or audio_id == "":
                continue
            duration = row.get("duration_in_ms")
            found.append(
                IgAudio(
                    audio_id=str(audio_id),
                    title=_text(row.get("title")) or "",
                    display_artist=_text(row.get("display_artist")),
                    ig_username=_text(row.get("ig_username")),
                    audio_type=_text(row.get("audio_type")) or audio_type,
                    duration_in_ms=None if isinstance(duration, bool) else _int(duration),
                    on_platform_audio_preview_link=_text(row.get("on_platform_audio_preview_link")),
                )
            )
        return found

    # Posting

    def publishing_limit(self, host: str, ig_id: str, token: str) -> tuple[int, int]:
        """Posts made in the last 24 hours, and how many Meta allows."""
        url = self._url(host, ig_id, "content_publishing_limit")
        body = self._request("GET", url, token, params={"fields": "config,quota_usage"})
        row = (body.get("data") or [{}])[0]
        total = _int((row.get("config") or {}).get("quota_total")) or DEFAULT_QUOTA
        return _int(row.get("quota_usage")) or 0, total

    def create_container(self, host: str, ig_id: str, token: str, **fields: Any) -> Container:
        body = self._request("POST", self._url(host, ig_id, "media"), token, data=_form(fields))
        return Container(id=_required(body, "id"), uri=body.get("uri"))

    def upload(self, uri: str, token: str, path: Path, *, offset: int) -> None:
        """The video's bytes from ``offset`` on, to the upload ``uri`` Meta gave, which must be on Meta's
        upload host."""
        parts = urlsplit(uri)
        if parts.scheme != "https" or parts.hostname != UPLOAD_HOST or parts.port is not None:
            raise GraphError(0, None, None, "The upload address isn't Meta's upload host.")
        size = path.stat().st_size
        headers = {"offset": str(offset), "file_size": str(size), "Content-Length": str(size - offset)}
        self._request(
            "POST", uri, token, content=_chunks(path, offset), headers=headers, timeout=UPLOAD_TIMEOUT
        )

    def upload_offset(self, container_id: str, token: str) -> int:
        """How many of the video's bytes Meta has, to carry on from."""
        url = self._url(GRAPH_FB, container_id)
        body = self._request("GET", url, token, params={"fields": "id,status_code,video_status"})
        phase = (body.get("video_status") or {}).get("uploading_phase") or {}
        return _int(phase.get("bytes_transferred")) or 0

    def container_status(self, host: str, container_id: str, token: str) -> ContainerStatus:
        body = self._request("GET", self._url(host, container_id), token, params={"fields": STATUS_FIELDS})
        found = body.get("copyright_check_status")
        return ContainerStatus(
            status_code=str(body.get("status_code") or "IN_PROGRESS"),
            status=clean_text(body.get("status")),
            copyright=found if isinstance(found, dict) else {},
        )

    def publish(self, host: str, ig_id: str, container_id: str, token: str) -> tuple[str, list[str]]:
        """The container made public: the media id, and Meta's ``config_issue`` notes."""
        url = self._url(host, ig_id, "media_publish")
        body = self._request("POST", url, token, params={"creation_id": container_id})
        return _required(body, "id"), _issues(body.get("config_issue"))

    def recent_media(self, host: str, ig_id: str, token: str, limit: int = 10) -> list[dict[str, Any]]:
        url = self._url(host, ig_id, "media")
        body = self._request("GET", url, token, params={"fields": RECENT_FIELDS, "limit": limit})
        return [m for m in body.get("data") or [] if isinstance(m, dict)]

    def media(self, host: str, media_id: str, token: str) -> dict[str, Any]:
        return self._request("GET", self._url(host, media_id), token, params={"fields": MEDIA_FIELDS})

    def insights(self, host: str, media_id: str, token: str) -> dict[str, int]:
        """The reel's counts by metric name, those Meta gave as whole numbers of 0 or more."""
        url = self._url(host, media_id, "insights")
        body = self._request("GET", url, token, params={"metric": ",".join(INSIGHT_METRICS)})
        counts: dict[str, int] = {}
        for item in body.get("data") or []:
            if not isinstance(item, dict) or item.get("name") not in INSIGHT_METRICS:
                continue
            values, total = item.get("values"), item.get("total_value")
            if isinstance(values, list) and values and isinstance(values[0], dict):
                value = values[0].get("value")
            else:
                value = total.get("value") if isinstance(total, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                counts[item["name"]] = value
        return counts
