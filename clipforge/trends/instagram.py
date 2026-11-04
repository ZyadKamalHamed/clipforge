"""Instagram's trending sounds (SPEC 13): for each account linked through Facebook (Instagram's Audio API
isn't offered to accounts on Instagram Login), both lists, music and original sounds, read once a day from
the morning's read time in the posting time zone. What Meta sends is untrusted: the text is cleaned and
cut, a preview link is kept only on instagram.com, and the sound's file, artwork and pictures are never
asked for. A failed read keeps the lists from before and tries again the next day, or on Check now."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from clipforge.db import transaction
from clipforge.errors import ClipForgeError
from clipforge.post import keychain
from clipforge.post import store as post_store
from clipforge.post.graph import GRAPH_FB, GraphClient, GraphError, IgAudio
from clipforge.post.models import Account
from clipforge.repo import new_id
from clipforge.trends import store
from clipforge.trends.models import AudioType, Sound, TrendRead
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine

log = logging.getLogger(__name__)

AUDIO_TYPES: tuple[AudioType, ...] = ("music", "original_sound")
TITLE_CHARS = 200  # a title or artist longer than this is cut
UNTITLED = "Untitled sound"
PREVIEW_HOSTS = ("instagram.com", "www.instagram.com")
ONLY_FACEBOOK = "Instagram only adds sounds to accounts linked through Facebook."
READ_FAILED = "Instagram's trending sounds couldn't be read. ClipForge tries again tomorrow."
READING = "Instagram's trending sounds are being read now."

_lock = threading.Lock()  # one read at a time, whether the timer's or a Check now


class Busy(ClipForgeError):
    """Another read of Instagram's trending sounds is running."""

    def __init__(self) -> None:
        super().__init__("trends_busy", READING)


def read_id(account_id: str) -> str:
    return f"ig:{account_id}"


def _linked(account: Account) -> bool:
    """Whether Instagram lists sounds for the account, the Keychain aside."""
    route = (account.platform, account.method, account.ig_mode)
    return route == ("instagram", "api", "facebook") and bool(account.token_ref and account.ig_user_id)


def can_read(account: Account) -> bool:
    """Whether the account's trending sounds can be read: Instagram, linked through Facebook, with its
    token in the Keychain."""
    return _linked(account) and bool(keychain.get_token(account.token_ref))  # type: ignore[arg-type]


def _text(value: str | None) -> str | None:
    text = " ".join((clean_text(value) or "").split())
    return text[:TITLE_CHARS] or None


def _link(value: str | None) -> str | None:
    """The preview link when it is https on instagram.com, else None (the sound is shown as text)."""
    if not value or not value.isprintable() or " " in value:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.hostname not in PREVIEW_HOSTS or port is not None:
        return None
    return value


def _row(audio: IgAudio, rank: int, account_id: str, now: float) -> Sound:
    duration = audio.duration_in_ms
    return Sound(
        id=new_id("snd"),
        platform="instagram",
        source="ig_trending",
        external_id=audio.audio_id,
        title=_text(audio.title) or UNTITLED,
        artist=_text(audio.display_artist) or _text(audio.ig_username),
        url=_link(audio.on_platform_audio_preview_link),
        duration_s=duration / 1000 if duration is not None and duration > 0 else None,
        rank=rank,
        account_id=account_id,
        created_at=now,
    )


def read_account(engine: Engine, account_id: str) -> TrendRead:
    """Reads the account's two trending lists in place of the ones before, only when both came back, and
    records the read. Raises ClipForgeError when the account can't be read, and Busy while another read
    runs."""
    if not _lock.acquire(blocking=False):
        raise Busy()
    try:
        return _read(engine, account_id)
    finally:
        _lock.release()


def _read(engine: Engine, account_id: str) -> TrendRead:
    conn = engine.db()
    account = post_store.get_account(conn, account_id)
    if account is None or not can_read(account):
        raise ClipForgeError("no_trending", ONLY_FACEBOOK)
    now = engine.now().timestamp()
    token: Any = keychain.get_token(account.token_ref)  # type: ignore[arg-type]
    ig_id: Any = account.ig_user_id  # _linked checked it is there
    lists: dict[AudioType, list[IgAudio]] = {}
    try:
        with GraphClient(engine.settings.posting.graph_version, transport=engine.graph_transport) as graph:
            for audio_type in AUDIO_TYPES:
                lists[audio_type] = graph.ig_audio(GRAPH_FB, ig_id, token, audio_type)
    except GraphError as err:
        log.warning(
            "Instagram's trending sounds for %s weren't read (%s, HTTP %s)", account_id, err.code, err.status
        )
        return _failed(engine, account_id, now)
    else:
        read = TrendRead(id=read_id(account_id), read_at=now, result="ok")
        with transaction(conn):
            for audio_type, found in lists.items():
                rows = [_row(audio, rank, account_id, now) for rank, audio in enumerate(found)]
                store.replace_trending(conn, account_id, audio_type, rows, now)
            store.save_read(conn, read)
    engine.events.publish("trends", {"account_id": account_id, "result": read.result})
    return read


def _failed(engine: Engine, account_id: str, now: float) -> TrendRead:
    """Records a read that failed, so the next one waits for tomorrow's read time or Check now."""
    read = TrendRead(id=read_id(account_id), read_at=now, result="error", error=READ_FAILED)
    store.save_read(engine.db(), read)
    engine.events.publish("trends", {"account_id": account_id, "result": read.result})
    return read


def due(engine: Engine, account: Account, now: datetime) -> bool:
    """Whether the account's lists are to be read at ``now``: not yet read today in the posting time zone,
    and the morning's read time has come."""
    if not _linked(account):
        return False
    zone = ZoneInfo(engine.settings.posting.timezone)
    today = now.astimezone(zone).date()
    if now < datetime.combine(today, time.fromisoformat(engine.settings.briefs.read_at), zone):
        return False
    last = store.read_of(engine.db(), read_id(account.id))
    if (
        last is not None
        and last.read_at is not None
        and datetime.fromtimestamp(last.read_at, zone).date() == today
    ):
        return False
    return can_read(account)


def tick(engine: Engine) -> int:
    """The timer's look at the clock: reads each account that is due. How many it read; a Check now still
    running leaves the rest for the next tick, and anything else going wrong with one account (the
    Keychain, say) is recorded as its failed read, so the others are still read and it waits for
    tomorrow."""
    now = engine.now()
    done = 0
    for account in post_store.list_accounts(engine.db()):
        try:
            if not due(engine, account, now):
                continue
            read_account(engine, account.id)
        except Busy:
            break
        except Exception as err:  # one account's trouble never stops the others
            log.warning(
                "Instagram's trending sounds for %s weren't read (%s)", account.id, type(err).__name__
            )
            _failed(engine, account.id, now.timestamp())
            continue
        done += 1
    return done
