"""Meta's Graph API as far as ClipForge uses it, for ``httpx.MockTransport``: the happy path by default,
scripted answers where a test needs something else, and every request kept to check afterwards."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx

USER_TOKEN = "user-token-for-tests-0001"
PAGE_TOKEN = "page-token-for-tests-0001"
IG_TOKEN = "ig-token-for-tests-0001"
APP_SECRET = "app-secret-for-tests-0001"
TOKENS = (USER_TOKEN, PAGE_TOKEN, IG_TOKEN, APP_SECRET)

RUPLOAD = "rupload.facebook.com"
COUNTS = {"views": 1200, "likes": 80, "comments": 5, "shares": 3, "saved": 9}  # a reel's insights


def page(n: int, *, ig: bool = True) -> dict[str, Any]:
    """One Facebook Page from ``/me/accounts``, with its Instagram account unless ``ig`` is False."""
    p: dict[str, Any] = {"id": f"pg{n}", "name": f"Page {n}", "access_token": f"{PAGE_TOKEN}-{n}"}
    if ig:
        p["instagram_business_account"] = {
            "id": f"ig{n}",
            "username": f"clips_{n}",
            "profile_picture_url": f"https://scontent.example/{n}.jpg",
        }
    return p


def error(
    code: int | None, subcode: int | None = None, message: str = "Something went wrong", status: int = 400
):
    """A Graph error answer, as Meta sends them."""
    body: dict[str, Any] = {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "AbC1"}
    if subcode is not None:
        body["error_subcode"] = subcode
    return httpx.Response(status, json={"error": body})


def audio_row(audio_id: str, **fields: Any) -> dict[str, Any]:
    """A trending sound as Meta's ``ig_audio`` gives it, with the file, artwork and picture links ClipForge
    never keeps."""
    return {
        "audio_id": audio_id,
        "title": f"Song {audio_id}",
        "display_artist": "The Artist",
        "ig_username": "the_artist",
        "audio_type": "music",
        "duration_in_ms": 30500,
        "on_platform_audio_preview_link": f"https://www.instagram.com/reels/audio/{audio_id}/",
        "download_url": f"https://cdn.example/{audio_id}.m4a",
        "cover_artwork_thumbnail_uri": f"https://cdn.example/{audio_id}.jpg",
        "profile_picture_url": "https://cdn.example/artist.jpg",
        "is_ads_eligible": True,
    } | fields


def graph_time(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


class FakeGraph:
    def __init__(self, now: Callable[[], datetime] | None = None):
        self.now = now or (lambda: datetime.now(UTC))
        self.requests: list[httpx.Request] = []
        self.script: dict[str, list[Any]] = defaultdict(list)  # answers given first, per kind of call
        self.page_sets: list[list[dict[str, Any]]] = [[page(1), page(2, ig=False)], [page(3)]]
        self.next_host = "graph.facebook.com"
        self.me: dict[str, Any] = {"user_id": "17841400", "username": "clips_b", "account_type": "BUSINESS"}
        self.quota = (0, 50)
        self.statuses = ["FINISHED"]  # each status read takes the next one; the last one stays
        self.copyright: dict[str, Any] = {"status": "completed", "matches_found": False}
        self.config_issue: Any = None
        self.containers: dict[str, dict[str, str]] = {}  # each container's fields
        self.published: list[str] = []  # the containers published, in order
        self.recent: list[dict[str, Any]] = []
        self.counts: dict[str, dict[str, int]] = {}  # each reel's insights by media id, when not COUNTS
        self.bodies: list[bytes] = []  # each upload's bytes
        self.transferred = 0  # what the last upload left on Meta's side
        self.refreshes = 0
        self.on_refresh: Callable[[], None] | None = None
        self.audio: dict[str, list[dict[str, Any]]] = {}  # the trending list per audio_type

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def kind(self, request: httpx.Request) -> str:
        path, fields = request.url.path, request.url.params.get("fields", "")
        if request.url.host == RUPLOAD:
            return "upload"
        if path.endswith("/me/accounts"):
            return "pages"
        if path.endswith("/oauth/access_token"):
            return "exchange"
        if path == "/refresh_access_token":
            return "refresh"
        if path.endswith("/me"):
            return "me"
        if path.endswith("/content_publishing_limit"):
            return "limit"
        if path.endswith("/media_publish"):
            return "publish"
        if path.endswith("/insights"):
            return "insights"
        if path.endswith("/ig_audio"):
            return "audio"
        if path.endswith("/media"):
            return "container" if request.method == "POST" else "recent"
        if "video_status" in fields:
            return "offset"
        if "status_code" in fields:
            return "status"
        return "media"

    def calls(self) -> list[str]:
        return [self.kind(r) for r in self.requests]

    def of(self, kind: str) -> list[httpx.Request]:
        return [r for r in self.requests if self.kind(r) == kind]

    def handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        kind = self.kind(request)
        if self.script[kind]:
            answer = self.script[kind].pop(0)
            if isinstance(answer, BaseException):
                raise answer
            if callable(answer):
                answer = answer(request)
            if answer is not None:
                return answer
        return getattr(self, f"_{kind}")(request)

    @staticmethod
    def form(request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}

    def _pages(self, request: httpx.Request) -> httpx.Response:
        n = int(request.url.params.get("after", "0"))
        body: dict[str, Any] = {"data": self.page_sets[n]}
        if n + 1 < len(self.page_sets):
            nxt = f"https://{self.next_host}/v25.0/me/accounts?after={n + 1}&access_token={USER_TOKEN}"
            body["paging"] = {"cursors": {"after": str(n + 1)}, "next": nxt}
        return httpx.Response(200, json=body)

    def _exchange(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": USER_TOKEN, "token_type": "bearer", "expires_in": 5183944}
        )

    def _me(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.me)

    def _refresh(self, request: httpx.Request) -> httpx.Response:
        self.refreshes += 1
        if self.on_refresh is not None:
            self.on_refresh()
        token = f"{IG_TOKEN}-refreshed-{self.refreshes}"
        return httpx.Response(
            200, json={"access_token": token, "token_type": "bearer", "expires_in": 5184000}
        )

    def _limit(self, request: httpx.Request) -> httpx.Response:
        used, total = self.quota
        config = {"quota_total": total, "quota_duration": 86400}
        return httpx.Response(200, json={"data": [{"quota_usage": used, "config": config}]})

    def _container(self, request: httpx.Request) -> httpx.Response:
        cid = f"c{len(self.containers) + 1}"
        fields = self.form(request)
        self.containers[cid] = fields
        body = {"id": cid}
        if fields.get("upload_type") == "resumable":
            body["uri"] = f"https://{RUPLOAD}/ig-api-upload/v25.0/{cid}"
        return httpx.Response(200, json=body)

    def _upload(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(request.content)
        self.transferred = int(request.headers["offset"]) + len(request.content)
        return httpx.Response(200, json={"success": True, "message": "Upload successful."})

    def _offset(self, request: httpx.Request) -> httpx.Response:
        phase = {"status": "in_progress", "bytes_transferred": self.transferred}
        cid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200, json={"id": cid, "status_code": "IN_PROGRESS", "video_status": {"uploading_phase": phase}}
        )

    def _status(self, request: httpx.Request) -> httpx.Response:
        cid = request.url.path.rsplit("/", 1)[-1]
        code = "PUBLISHED" if cid in self.published else self.statuses[0]
        if len(self.statuses) > 1 and cid not in self.published:
            self.statuses.pop(0)
        body = {
            "id": cid,
            "status_code": code,
            "status": code.title(),
            "copyright_check_status": self.copyright,
        }
        return httpx.Response(200, json=body)

    def _publish(self, request: httpx.Request) -> httpx.Response:
        cid = request.url.params["creation_id"]
        self.published.append(cid)
        mid = f"m{len(self.published)}"
        caption = self.containers.get(cid, {}).get("caption", "")
        self.recent.insert(0, {"id": mid, "caption": caption, "timestamp": graph_time(self.now())})
        body: dict[str, Any] = {"id": mid}
        if self.config_issue is not None:
            body["config_issue"] = self.config_issue
        return httpx.Response(200, json=body)

    def _recent(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": self.recent})

    def _insights(self, request: httpx.Request) -> httpx.Response:
        mid = request.url.path.rsplit("/", 2)[-2]
        counts = self.counts.get(mid, COUNTS)
        wanted = request.url.params.get("metric", "").split(",")
        data = [
            {"name": n, "period": "lifetime", "values": [{"value": counts[n]}], "id": f"{mid}/insights/{n}"}
            for n in wanted
            if n in counts
        ]
        return httpx.Response(200, json={"data": data})

    def _audio(self, request: httpx.Request) -> httpx.Response:
        """Meta's whole row, whatever ``fields`` asks for, so a test can see ClipForge keeps only its own."""
        return httpx.Response(
            200, json={"data": self.audio.get(request.url.params.get("audio_type", ""), [])}
        )

    def _media(self, request: httpx.Request) -> httpx.Response:
        mid = request.url.path.rsplit("/", 1)[-1]
        body = {
            "id": mid,
            "permalink": f"https://www.instagram.com/reel/{mid}/",
            "media_product_type": "REELS",
        }
        return httpx.Response(200, json=body | {"timestamp": graph_time(self.now())})


def lost(published: FakeGraph | None = None) -> Callable[[httpx.Request], httpx.Response]:
    """An answer that never comes back: the call reaches Meta (published, when ``published`` is the
    graph), then the connection drops."""

    def answer(request: httpx.Request) -> httpx.Response:
        if published is not None:
            published._publish(request)
        raise httpx.ReadTimeout("the answer never came", request=request)

    return answer
