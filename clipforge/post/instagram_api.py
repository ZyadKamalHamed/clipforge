"""Instagram through Meta's official API (SPEC 10.2): linking accounts from a pasted token (Mode A through
a Facebook Page, Mode B through Instagram Login), keeping Mode B's tokens fresh, and the publisher that
posts a Reel. Each step is checkpointed in ``post.state`` before it is taken, so a post that stopped
carries on where it was and ``media_publish`` is never sent twice."""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar
from zoneinfo import ZoneInfo

from clipforge import repo
from clipforge.db import transaction
from clipforge.errors import ClipForgeError, WaitUntil
from clipforge.post import keychain, queue, store
from clipforge.post.distribute import full_caption
from clipforge.post.errors import Fatal, NeedsUser, Retryable
from clipforge.post.export import NO_VIDEO
from clipforge.post.graph import (
    GRAPH_FB,
    GRAPH_IG,
    SUBCODES,
    ContainerStatus,
    GraphClient,
    GraphError,
    PageCandidate,
    action_for,
)
from clipforge.post.models import Account, Capabilities, Health, Post, PublishResult
from clipforge.post.plan import CAPS, at_text
from clipforge.post.public_media import UNREACHABLE, public_host, public_url, self_test

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.post.publisher import Report

log = logging.getLogger(__name__)
T = TypeVar("T")

POLL_S = 15.0  # how often a container being processed is read
POLL_MAX_S = 600.0  # and for how long
CHECK_S = 60.0  # a publish whose answer was lost is checked after this
RATE_WAIT_S = 3600.0
LATER_S = 86400.0  # Meta's 24 hour posting window
TICKET_S = 600.0  # how long the accounts read from a pasted token wait to be picked
REFRESH_AFTER_S = 7 * 86400.0
EXPIRING_S = 10 * 86400.0
TOKEN_LIFE_S = 60 * 86400.0
MATCH_SLACK_S = 60.0  # a post found on Instagram may say it went up this long before the publish was asked
PROFESSIONAL = ("BUSINESS", "MEDIA_CREATOR")
COPYRIGHT_ACTIONS = ("publish_anyway", "cancel")
TRIAL = {"manual": "MANUAL", "auto": "SS_PERFORMANCE"}  # Meta's graduation strategies for a trial reel
# What Instagram has of a post, down to whether it was asked to publish the container
SENT = ("container", "uri", "upload_started", "uploaded", "renewed", "fetched", "asked")
PERMALINK = re.compile(r"^https://www\.instagram\.com/[A-Za-z0-9_/.-]+$")

NO_TOKEN = "ClipForge has no key for this account. Reconnect {how} in Accounts, then press Retry."
RECONNECT = "Instagram stopped accepting this account's key. Reconnect {how} in Accounts, then press Retry."
OPEN_APP = "Open the Instagram app on your phone, clear what it asks, then press Retry."
SPAM = (
    "Instagram flagged this account for suspected spam, so ClipForge paused it. "
    "Nothing more posts to it until you resume it."
)
PAUSE_NOTES = (OPEN_APP, SPAM)  # the sentences ClipForge pauses an account with
LATER = "Instagram's 24 hour posting limit for this account was reached, so this post moved to {new}."
CAP_REACHED = "Instagram's 24 hour posting limit for this account was reached. Press Retry tomorrow."
SLOW = "Instagram asked ClipForge to slow down, so it tries again in an hour."
BUSY = "Instagram didn't answer properly, so ClipForge tries again shortly."
PROCESSING = "Instagram is processing the video."
CHECKING = "Checking whether Instagram posted this."
TIMEOUT = "Instagram took more than 10 minutes to process this video. Press Retry to send it again."
UPLOAD_FAILED = "Instagram couldn't process this video. Press Retry to send it again."
COPYRIGHT = "Instagram found material in this clip that someone else owns. Publish it anyway, or cancel it."
UNSURE = "Instagram may already have posted this. Check the account, then press Retry or Mark as posted."
NOT_FOUND = "Instagram shows this as posted, but ClipForge couldn't find its link."
CAPTION_NOTE = "Instagram posted this without its caption. Add it in the Instagram app."
ALREADY_ASKED = "Instagram was already asked to post this with its caption, so the caption can't change here."
REJECTED = "Instagram turned this post down: {why}"
NO_PUBLIC_URL = (
    "Posting through Instagram Login needs a public address for this Mac. "
    "Set it in Settings, then press Retry."
)
SOUND_LOST = (
    "Instagram only adds sounds to accounts linked through Facebook, and this one is linked through "
    "Instagram now. Use Post without the sound, or link it through Facebook again."
)
TOKEN_REJECTED = "Instagram didn't accept that key: {why}"
NOT_PROFESSIONAL = (
    "This is a personal Instagram account. Switch it to a Creator or Business account in the Instagram "
    "app, then paste its key again."
)
OTHER_ACCOUNT = "That key is for @{got}, not this account. Paste the key for @{want}."
LINK_EXPIRED = "That list of accounts is more than 10 minutes old. Paste the key again."
EXPIRING = (
    "This account's key runs out on {day}, and refreshing it keeps failing. Reconnect Instagram before then."
)


def caps_for(account: Account) -> Capabilities:
    """What the route takes for ``account``: Mode A sends the file itself, Mode B a public address."""
    mode_b = account.ig_mode == "instagram"
    return CAPS["instagram:api"].model_copy(update={"local_file": not mode_b, "needs_public_url": mode_b})


def _how(account: Account) -> str:
    return "Instagram" if account.ig_mode == "instagram" else "Facebook"


def _host(account: Account) -> str:
    return GRAPH_IG if account.ig_mode == "instagram" else GRAPH_FB


def _client(engine: Engine) -> GraphClient:
    return GraphClient(engine.settings.posting.graph_version, transport=engine.graph_transport)


def _token(account: Account) -> str | None:
    return keychain.get_token(account.token_ref) if account.token_ref else None


def _set_account(engine: Engine, account_id: str, **fields: Any) -> Account:
    return queue.change_account(engine, account_id, **fields)


def _graph_time(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        return None


def _subcode(status: str | None) -> int | None:
    found = re.search(r"\b(2207\d{3})\b", status or "")
    return int(found.group(1)) if found else None


class InstagramGraphPublisher:
    platform = "instagram"
    method = "api"
    caps = CAPS["instagram:api"]

    def __init__(self, engine: Engine):
        self.engine = engine

    def check(self, account: Account) -> Health:
        token = _token(account)
        if token is None:
            return Health(status="needs_login", sentence=NO_TOKEN.format(how=_how(account)))
        try:
            with _client(self.engine) as graph:
                if account.ig_mode == "instagram":
                    graph.ig_me(token)
                    return Health(status="connected", sentence="Connected.")
                used, total = graph.publishing_limit(GRAPH_FB, account.ig_user_id or "", token)
        except GraphError as err:
            if action_for(err) == "reconnect":
                return Health(
                    status="needs_login", sentence=RECONNECT.format(how=_how(account)), details=str(err)
                )
            return Health(status="error", sentence=BUSY, details=str(err))
        left = max(total - used, 0)
        return Health(status="connected", sentence=f"Connected. Instagram allows {left} more posts today.")

    def publish(self, post: Post, report: Report) -> PublishResult:
        account = report.account
        token = _token(account)
        if token is None:
            sentence = NO_TOKEN.format(how=_how(account))
            _set_account(self.engine, account.id, status="needs_login", status_note=sentence)
            raise NeedsUser(sentence)
        with _client(self.engine) as graph:
            return _Publish(self.engine, graph, token, report).go()

    def resumes_on_retry(self, post: Post, account: Account) -> bool:
        """Whether trying the post again turns its account back on: ClipForge paused it because Instagram
        asked for its app, and the post's sentence says to clear that, then press Retry."""
        return account.paused and account.status_note == OPEN_APP and post.error == OPEN_APP

    def edit_state(self, post: Post) -> dict[str, Any]:
        """What a post whose words changed keeps: its container holds the old caption, so it is made again.
        Refused once Instagram was asked to publish it, since it may be up already."""
        if any(post.state.get(k) for k in ("media_id", "publish_requested_at", "asked")):
            raise ClipForgeError("already_asked", ALREADY_ASKED)
        return {k: v for k, v in post.state.items() if k not in (*SENT, "poll_started")}

    def retry_state(self, post: Post, state: dict[str, Any]) -> dict[str, Any]:
        """What a post tried again keeps: one Instagram posted finishes, one the person checked wasn't
        posted is published again from its container, and one that failed is sent again from the start,
        unless its container was asked to publish: that one is read first, since it may have gone up."""
        if state.get("media_id"):
            return state
        drop = {"publish_requested_at", "unsure", "poll_started"}
        if post.status == "failed" and not state.get("asked"):
            drop |= set(SENT)
        return {k: v for k, v in state.items() if k not in drop}


def _trial(post: Post) -> str | None:
    """A trial reel's ``trial_params``: how it graduates to the account's followers, or None for a Reel."""
    if not post.options.trial_reel:
        return None
    return json.dumps({"graduation_strategy": TRIAL[post.options.trial_graduation]})


def _audio(post: Post) -> str | None:
    """The trending sound picked for the post as Meta's ``audio_configuration``, or None for none. Only
    Mode A takes one: the plan refuses a sound for any other account."""
    sound = post.options.sound
    if sound is None or sound.source != "ig_trending":
        return None
    picked = {"audio_id": sound.external_id, "video_volume": 100, "audio_volume": sound.volume}
    return json.dumps(picked, separators=(",", ":"))


class _Publish:
    """One go at a post, from wherever its state says it got to."""

    def __init__(self, engine: Engine, graph: GraphClient, token: str, report: Report):
        self.engine = engine
        self.graph = graph
        self.token = token
        self.report = report
        self.account = report.account
        self.host = _host(report.account)
        self.ig_id = report.account.ig_user_id or ""
        self.caption = full_caption(report.post.caption, report.post.hashtags)
        self.mode_b = report.account.ig_mode == "instagram"  # Instagram fetches the video from a URL

    @property
    def state(self) -> dict[str, Any]:
        return self.report.post.state

    def now(self) -> float:
        return self.engine.now().timestamp()

    def go(self) -> PublishResult:
        if self.state.get("media_id"):
            return self._done(self.state["media_id"], [])
        if self.state.get("publish_requested_at") is not None:
            return self._resume()
        if self.state.get("container") and self._address_lost():
            self.report.step("new_address")
            self.report.drop(*SENT, "poll_started")
        if not self.state.get("container"):
            if self.mode_b and _audio(self.report.post) is not None:  # picked before a relink
                raise NeedsUser(SOUND_LOST)
            if self.mode_b and not self.engine.settings.posting.public_base_url:
                raise NeedsUser(NO_PUBLIC_URL)
            self._check_quota()
            self._make_container()
        if not self.state.get("uploaded"):
            self._upload()
        self.report.processing()
        status = self._poll()
        if status is None:  # made again: start over with the new container
            return self.go()
        if status.status_code == "PUBLISHED":  # a publish asked for on an earlier go went up after all
            return self._found()
        self._check_copyright(status)
        self.report.final_check()
        return self._publish()

    # Each step

    def _call(self, step: Callable[[], T]) -> T:
        try:
            return step()
        except GraphError as err:
            self._fail(err)

    def _check_quota(self) -> None:
        used, total = self._call(lambda: self.graph.publishing_limit(self.host, self.ig_id, self.token))
        self.report.step("limit", used=used, total=total)
        if used >= total:
            self._later(f"{used} of {total} posts in the last 24 hours")

    def _make_container(self) -> None:
        post = self.report.post
        render = repo.get_render(self.engine.db(), post.render_id) if post.render_id else None
        cover = render.cover_s if render is not None else None
        fields = {
            "media_type": "REELS",
            "upload_type": None if self.mode_b else "resumable",
            "video_url": self._public_url() if self.mode_b else None,
            "caption": self.caption,
            "share_to_feed": None if post.options.trial_reel else post.options.share_to_feed,
            "trial_params": _trial(post),
            "thumb_offset": round(cover * 1000) if cover is not None else None,
            "audio_configuration": None if self.mode_b else _audio(post),
        }
        self.report.step("container")
        made = self._call(lambda: self.graph.create_container(self.host, self.ig_id, self.token, **fields))
        self.report.save(
            container=made.id, uri=made.uri, upload_started=False, uploaded=self.mode_b, poll_started=None
        )

    def _public_url(self) -> str:
        """A new public address for the video, fetched once as Meta would before Meta is told of it."""
        video = self.report.video
        if video is None or not video.is_file():
            raise Fatal(NO_VIDEO, "no_render")
        settings, media = self.engine.settings.posting, self.engine.public_media
        url = public_url(settings, media.issue(self.report.post.id, video))
        problem = self_test(url, video.stat().st_size, transport=self.engine.public_transport)
        self.report.step("self_test", ok=problem is None, problem=problem)
        if problem is not None:
            media.revoke(self.report.post.id)
            raise NeedsUser(UNREACHABLE.format(host=public_host(settings)), details=problem)
        return url

    def _address_lost(self) -> bool:
        """A Mode B container Instagram hasn't finished, whose address went with a restart."""
        return (
            self.mode_b
            and not self.state.get("fetched")
            and not self.engine.public_media.live(self.report.post.id)
        )

    def _upload(self) -> None:
        video = self.report.video
        if video is None or not video.is_file():
            raise Fatal(NO_VIDEO, "no_render")
        container, uri = self.state["container"], self.state.get("uri")
        if not uri:
            raise Fatal(UPLOAD_FAILED, "upload_failed", details="Meta gave no upload address.")
        offset = 0
        if self.state.get("upload_started"):  # carrying on: from what Meta already has
            offset = self._call(lambda: self.graph.upload_offset(container, self.token))
        else:
            self.report.save(upload_started=True)
        size = video.stat().st_size
        self.report.step("upload", offset=offset, size=size)
        if offset < size:
            self._call(lambda: self.graph.upload(uri, self.token, video, offset=offset))
        self.report.save(uploaded=True)
        self.report.progress(0.5, PROCESSING)

    def _poll(self) -> ContainerStatus | None:
        """The container once Instagram has finished with it. Raises WaitUntil to read it again in 15
        seconds; None when it was made again."""
        started = self.state.get("poll_started")
        if started is None:
            started = self.now()
            self.report.save(poll_started=started)
        container = self.state["container"]
        try:
            status = self.graph.container_status(self.host, container, self.token)
        except GraphError as err:
            if action_for(err) == "poll":
                status = ContainerStatus(status_code="IN_PROGRESS")
            else:
                self._fail(err)
        code = status.status_code
        self.report.step("poll", status=code, copyright=status.copyright.get("status"))
        if code == "FINISHED" and self.mode_b and not self.state.get("fetched"):
            self.report.save(fetched=True)  # Instagram has the video: its address is no longer needed
        if code == "PUBLISHED" or (
            code == "FINISHED" and status.copyright.get("status") not in ("in_progress", "not_started")
        ):
            return status
        if code in ("ERROR", "EXPIRED"):
            action = SUBCODES.get(_subcode(status.status) or 0)
            again = code == "EXPIRED" or action == "new_container" or (action == "retest_url" and self.mode_b)
            if again and not self.state.get("renewed"):
                self.report.step("new_container", status=status.status)
                self.report.drop(*SENT, "poll_started")
                self.report.save(renewed=True)
                return None
            if action == "retest_url":
                host = public_host(self.engine.settings.posting) or "this Mac"
                raise NeedsUser(UNREACHABLE.format(host=host), details=status.status)
            raise Fatal(UPLOAD_FAILED, "upload_failed", details=status.status)
        if self.now() - started >= POLL_MAX_S:
            self.report.save(poll_started=None)
            raise Fatal(TIMEOUT, "processing_timeout", details=f"Last status: {code}")
        raise WaitUntil(self.now() + POLL_S, PROCESSING)

    def _check_copyright(self, status: ContainerStatus) -> None:
        if status.copyright.get("matches_found") and not self.state.get("copyright_ok"):
            details = json.dumps(status.copyright, ensure_ascii=False)[:1000]
            raise NeedsUser(COPYRIGHT, details=details, actions=COPYRIGHT_ACTIONS)

    def _publish(self) -> PublishResult:
        now = self.now()
        self.report.step("publish")
        self.report.save(publish_requested_at=now, asked=now)
        try:
            media_id, issues = self.graph.publish(self.host, self.ig_id, self.state["container"], self.token)
        except GraphError as err:
            action = action_for(err)
            if action == "retry":  # the answer was lost or garbled, so Instagram may have it: checked later
                self.report.step("publish_unanswered", error=str(err))
                raise WaitUntil(now + CHECK_S, CHECKING) from None
            self.report.drop("publish_requested_at")  # a definite no: nothing went up
            if action == "poll":
                raise WaitUntil(now + POLL_S, PROCESSING) from None
            self._fail(err)
        self.report.save(media_id=media_id)
        return self._done(media_id, issues)

    def _resume(self) -> PublishResult:
        """A publish was asked for and no answer came: posted if Instagram says the container is, else the
        person checks, since sending it again could post it twice."""
        status = self._call(
            lambda: self.graph.container_status(self.host, self.state["container"], self.token)
        )
        self.report.step("resume", status=status.status_code)
        if status.status_code != "PUBLISHED":
            self.report.save(unsure=True)
            raise NeedsUser(UNSURE, details=f"The container's status is {status.status_code}.")
        return self._found()

    def _found(self) -> PublishResult:
        """A container Instagram shows as published: its post found on the account by caption and time, or
        posted without a link when none matches."""
        asked = self.state.get("publish_requested_at") or self.state.get("asked")
        for media in self._call(lambda: self.graph.recent_media(self.host, self.ig_id, self.token)):
            at = _graph_time(media.get("timestamp"))
            same = (media.get("caption") or "").strip() == self.caption.strip()
            recent = at is not None and (asked is None or at >= float(asked) - MATCH_SLACK_S)
            if same and recent and media.get("id"):
                self.report.save(media_id=str(media["id"]))
                return self._done(str(media["id"]), [])
        self.report.step("posted", media_id=None)
        return PublishResult(external_id=None, url=None, warnings=[NOT_FOUND])

    def _done(self, media_id: str, issues: list[str]) -> PublishResult:
        warnings = [CAPTION_NOTE] if any("CAPTION_NOT_ATTACHED" in i for i in issues) else []
        url = None
        try:
            found = self.graph.media(self.host, media_id, self.token).get("permalink")
            url = found if isinstance(found, str) and PERMALINK.match(found) else None
        except GraphError as err:  # posted all the same: only the link is missing
            self.report.step("permalink", error=str(err))
        self.report.step("posted", media_id=media_id)
        return PublishResult(external_id=media_id, url=url, warnings=warnings)

    # What Meta's errors do

    def _later(self, why: str) -> NoReturn:
        """The account's 24 hours are used up: the post moves a day on, or to the person once Instagram
        has the video, since a post being processed can't be moved."""
        engine, post_id = self.engine, self.report.post.id
        new = self.now() + LATER_S
        with engine.lock:
            current = store.get_post(engine.db(), post_id)
            if current.status != "uploading":
                raise NeedsUser(CAP_REACHED, details=why)
            tz = ZoneInfo(engine.settings.posting.timezone)
            note = LATER.format(new=at_text(datetime.fromtimestamp(new, UTC), tz))
            queue.move_post(engine, post_id, "scheduled", scheduled_at=new, note=note, details=why)
        raise WaitUntil(new, note)

    def _fail(self, err: GraphError) -> NoReturn:
        action, details = action_for(err), str(err)
        self.report.step("error", error=details, action=action)
        if action == "pause_fatal":
            _set_account(self.engine, self.account.id, paused=True, status_note=SPAM)
            raise Fatal(SPAM, "suspected_spam", details=details) from None
        if action == "open_app":
            _set_account(self.engine, self.account.id, paused=True, status_note=OPEN_APP)
            raise NeedsUser(OPEN_APP, details=details) from None
        if action == "reconnect":
            sentence = RECONNECT.format(how=_how(self.account))
            _set_account(self.engine, self.account.id, status="needs_login", status_note=sentence)
            raise NeedsUser(sentence, details=details) from None
        if action == "rate_limited":
            with self.engine.lock:
                queue.change_post(self.engine, self.report.post.id, note=SLOW)
            raise WaitUntil(self.now() + RATE_WAIT_S, SLOW) from None
        if action == "later_24h":
            self._later(details)
        if action in (
            "retry",
            "poll",
            "new_container",
            "retest_url",
        ):  # retest_url: the next try has a new URL
            raise Retryable(BUSY, details=details) from None
        raise Fatal(REJECTED.format(why=err.message), "rejected", details=details) from None


# Linking


def link_facebook(
    engine: Engine, user_token: str, *, app_id: str | None = None, app_secret: str | None = None
) -> tuple[str, list[PageCandidate]]:
    """The Instagram accounts on the person's Facebook Pages, held for 10 minutes under the ticket given
    back. With an app ID and secret the pasted token is exchanged for a long-lived one first; the secret
    and the user token are kept nowhere."""
    try:
        with _client(engine) as graph:
            if app_id and app_secret:
                user_token = graph.exchange(user_token, app_id, app_secret)
            found = graph.pages(user_token)
    except GraphError as err:
        raise ClipForgeError("token_rejected", TOKEN_REJECTED.format(why=err.message)) from None
    ticket = secrets.token_urlsafe(16)
    now = engine.now().timestamp()
    with engine.lock:
        for old in [t for t, (until, _) in engine.link_tickets.items() if until <= now]:
            del engine.link_tickets[old]
        engine.link_tickets[ticket] = (now + TICKET_S, found)
    return ticket, found


def _upsert(engine: Engine, account_id: str | None, ig_user_id: str, fields: dict[str, Any]) -> Account:
    """The Instagram API account ``account_id``, else the one already linked to ``ig_user_id``, else a new
    one, with ``fields``. All or nothing: a handle already here leaves no half-made account behind."""
    conn = engine.db()
    with transaction(conn):
        account = store.get_account(conn, account_id) if account_id else None
        if account is None:
            linked = [
                a
                for a in store.list_accounts(conn)
                if (a.platform, a.method, a.ig_user_id) == ("instagram", "api", ig_user_id)
            ]
            account = linked[0] if linked else None
        if account is None:
            made = Account(id=repo.new_id("a"), platform="instagram", method="api", created_at=time.time())
            account = store.add_account(conn, made)
        return store.update_account(conn, account.id, token_ref=keychain.ref_for(account.id), **fields)


def finish_facebook(engine: Engine, ticket: str, ig_user_ids: list[str]) -> list[Account]:
    """The picked accounts from ``ticket``'s list linked, each Page's token in the Keychain. A ticket is
    used once."""
    now = engine.now().timestamp()
    with engine.lock:
        held = engine.link_tickets.pop(ticket, None)
        if held is None or held[0] <= now:
            raise ClipForgeError("link_expired", LINK_EXPIRED)
        picked = set(ig_user_ids)
        made = []
        for c in held[1]:
            if c.ig_user_id not in picked:
                continue
            fields = {
                "ig_mode": "facebook",
                "ig_user_id": c.ig_user_id,
                "fb_page_id": c.page_id,
                "handle": c.username,
                "display_name": c.page_name,
                "status": "connected",
                "status_note": None,
                "last_checked_at": now,
            }
            account = _upsert(engine, None, c.ig_user_id, fields)
            keychain.put_token(account.token_ref, c.page_token)
            queue.account_event(engine, account)
            made.append(account)
    return made


def link_instagram(engine: Engine, token: str, account_id: str | None = None) -> Account:
    """A Mode B account linked, or ``account_id`` reconnected, from its pasted token, which must be a
    Business or Creator account's."""
    try:
        with _client(engine) as graph:
            me = graph.ig_me(token)
    except GraphError as err:
        raise ClipForgeError("token_rejected", TOKEN_REJECTED.format(why=err.message)) from None
    if me.account_type not in PROFESSIONAL:
        raise ClipForgeError("not_professional", NOT_PROFESSIONAL)
    now = engine.now().timestamp()
    with engine.lock:
        current = store.get_account(engine.db(), account_id) if account_id else None
        if current is not None and current.ig_user_id and current.ig_user_id != me.user_id:
            raise ClipForgeError("other_account", OTHER_ACCOUNT.format(got=me.username, want=current.handle))
        fields = {
            "ig_mode": "instagram",
            "ig_user_id": me.user_id,
            "fb_page_id": None,
            "handle": me.username,
            "status": "connected",
            "status_note": None,
            "token_expires_at": now + TOKEN_LIFE_S,
            "token_refreshed_at": now,
            "last_checked_at": now,
        }
        account = _upsert(engine, account_id, me.user_id, fields)
        keychain.put_token(account.token_ref, token)
        queue.account_event(engine, account)
    return account


# Mode B tokens

_refreshing: dict[str, threading.Lock] = {}
_refreshing_guard = threading.Lock()


def _refresh_lock(account_id: str) -> threading.Lock:
    with _refreshing_guard:
        return _refreshing.setdefault(account_id, threading.Lock())


def refresh_tokens(engine: Engine) -> None:
    """Each Mode B token older than 7 days refreshed, one refresh at a time per account. A failure 10 days
    or less from expiry warns on the account; a token Instagram refused asks for a reconnect."""
    now = engine.now().timestamp()
    with engine.lock:
        due = [
            a
            for a in store.list_accounts(engine.db())
            if (a.platform, a.method, a.ig_mode) == ("instagram", "api", "instagram")
            and a.status != "needs_login"
            and now - (a.token_refreshed_at or 0.0) > REFRESH_AFTER_S
        ]
    for account in due:
        lock = _refresh_lock(account.id)
        if not lock.acquire(blocking=False):
            continue
        try:
            _refresh(engine, account, now)
        finally:
            lock.release()


def _refresh(engine: Engine, account: Account, now: float) -> None:
    token = _token(account)
    try:
        if token is None:
            raise GraphError(0, 190, None, "No token in the Keychain")
        with _client(engine) as graph:
            new, seconds = graph.ig_refresh(token)
    except GraphError as err:
        log.warning("refreshing account %s's token failed: %s", account.id, err)
        if action_for(err) == "reconnect":
            _set_account(
                engine, account.id, status="needs_login", status_note=RECONNECT.format(how="Instagram")
            )
        elif account.token_expires_at is not None and account.token_expires_at - now <= EXPIRING_S:
            tz = ZoneInfo(engine.settings.posting.timezone)
            day = at_text(datetime.fromtimestamp(account.token_expires_at, UTC), tz)
            _set_account(engine, account.id, status="token_expiring", status_note=EXPIRING.format(day=day))
        return
    keychain.put_token(account.token_ref, new)
    fields: dict[str, Any] = {"token_refreshed_at": now, "token_expires_at": now + (seconds or TOKEN_LIFE_S)}
    if account.status == "token_expiring":
        fields |= {"status": "connected", "status_note": None}
    _set_account(engine, account.id, **fields)
