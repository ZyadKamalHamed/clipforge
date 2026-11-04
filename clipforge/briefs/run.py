"""The morning read (SPEC 10.11). Once a day, from the read time (6:00 am by default, on the posting time
zone's clock) and once the clipping channels' checks have made their drafts or an hour has passed, each
enabled source that isn't paste-only is read, one at a time: Campaign Scout's file, or an added site's list
through the general reader. What it found is merged and filled, and the source records when it ran, how it
went and, when it stopped, the sentence that says why. A source that stops waits for the next morning, or
for a Check now an hour after its last read; nothing is retried in between."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from clipforge.briefs import fill, general, merge, scout, store
from clipforge.briefs.fetch import Stop
from clipforge.briefs.models import BriefSource, Result
from clipforge.errors import ClipForgeError
from clipforge.watch import store as watch_store

if TYPE_CHECKING:
    from clipforge.app import Engine

log = logging.getLogger(__name__)

CHECKS_WAIT = timedelta(hours=1)  # the longest a read waits past the read time for the clipping checks
AGAIN = timedelta(hours=1)  # Check now at most once an hour per source
BUSY = "A read is already running."
TOO_SOON = "Read this source again after {time}."
TURNED_AWAY = "This site turned ClipForge away, so it waits for the next morning's read."
_TURNED_AWAY = ("blocked", "sign_in")  # no read in between: Check now waits for the next morning (10.11)
PASTE_ONLY = "This site is paste-only, so paste its briefs instead."
FAILED = "This source couldn't be read today. It tries again tomorrow morning."
_LIVE = ["new", "seen"]

_lock = threading.Lock()  # one read at a time, whether the morning's or a Check now
_reading: str | None = None


class Busy(ClipForgeError):
    """A read was asked for while another is running."""


class TooSoon(ClipForgeError):
    """Check now within an hour of the source's last read."""


class PasteOnly(ClipForgeError):
    """A read was asked of a source whose site forbids automated reading."""


@dataclass(frozen=True)
class Read:
    """How one read went: its result, how many briefs it added, changed and made gone, and the sentence (or
    Campaign Scout's problem) when it stopped."""

    result: Result
    new: int
    changed: int
    gone: int
    error: str | None


def _zone(engine: Engine) -> ZoneInfo:
    return ZoneInfo(engine.settings.posting.timezone)


def _day_of(engine: Engine, at: datetime) -> date:
    return at.astimezone(_zone(engine)).date()


def checks_waiting(engine: Engine, day: str) -> bool:
    """Whether one of the day's clipping checks is still running or still making its drafts."""
    return any(c.finished_at is None or c.drafts is None for c in watch_store.checks_on(engine.db(), day))


def read_time(engine: Engine, day: date) -> datetime:
    """When the day's read is due, on the posting time zone's clock."""
    return datetime.combine(day, time.fromisoformat(engine.settings.briefs.read_at), _zone(engine))


def _ran_on(engine: Engine, source: BriefSource, day: date) -> bool:
    """Whether the source was last read on ``day``, a date in the posting time zone."""
    if source.last_run_at is None:
        return False
    return datetime.fromtimestamp(source.last_run_at, _zone(engine)).date() == day


def due(engine: Engine, source: BriefSource, now: datetime) -> bool:
    """Whether ``source`` is to be read at ``now``: on, not paste-only, not yet read today, and the read time
    has come with the clipping checks done, or an hour after it."""
    if not source.enabled or source.reader == "paste":
        return False
    today = _day_of(engine, now)
    if _ran_on(engine, source, today):
        return False
    start = read_time(engine, today)
    if now < start:
        return False
    return now >= start + CHECKS_WAIT or not checks_waiting(engine, today.isoformat())


def reading_done(engine: Engine, day: str) -> bool:
    """Whether no source is still to be read on ``day``: always so for a day gone by."""
    now = engine.now()
    if _day_of(engine, now).isoformat() != day:
        return True
    return not any(due(engine, source, now) for source in store.sources(engine.db()))


def reading() -> str | None:
    """The id of the source being read, or None."""
    return _reading


def idle(timeout: float) -> bool:
    """Waits up to ``timeout`` seconds for the running read to finish: True once none is running."""
    if not _lock.acquire(timeout=timeout):
        return False
    _lock.release()
    return True


def check_now_after(engine: Engine, source: BriefSource) -> datetime | None:
    """When Check now opens for ``source`` again, or None when it is open now: an hour after its last read,
    or the next morning's read time after a site turned ClipForge away."""
    if source.last_run_at is None:
        return None
    last = datetime.fromtimestamp(source.last_run_at, _zone(engine))
    if source.last_result in _TURNED_AWAY:
        opens = read_time(engine, _day_of(engine, last) + timedelta(days=1))
    else:
        opens = last + AGAIN
    return opens if engine.now() < opens else None


def paste_today(engine: Engine, source: BriefSource) -> bool:
    """Whether the source's briefs are to be pasted today: an enabled paste-only source, or an added site
    whose read stopped today. Campaign Scout's marketplaces have paste-only sources of their own."""
    if not source.enabled:
        return False
    if source.reader == "paste":
        return True
    return source.reader == "general" and stopped_today(engine, source)


def stopped_today(engine: Engine, source: BriefSource) -> bool:
    """Whether the enabled source was read today and the read didn't go through: a site that refused or
    changed, or Campaign Scout's file missing, unreadable or a new shape."""
    if not source.enabled or source.reader == "paste":
        return False
    return _ran_on(engine, source, _day_of(engine, engine.now())) and source.last_result != "ok"


def _gather(engine: Engine, source: BriefSource) -> Read:
    """Reads ``source`` and merges what it found. A stop is the read's result, not an error."""
    if source.reader == "campaign_scout":
        got = scout.read_scout(scout.scout_file())
        if got.problem is not None:
            return Read("changed", 0, 0, 0, got.problem)
        found, complete = got.found, True
    elif source.reader == "general":
        try:
            found, complete = general.read_site(engine, source)
        except general.Cut as cut:  # what was read before the stop is kept; the rest waits for tomorrow
            merged = merge.merge(engine, source, cut.found, engine.now().timestamp(), complete=False)
            return Read(cut.result, len(merged.new), len(merged.changed), len(merged.gone), cut.message)
        except Stop as stop:
            return Read(stop.result, 0, 0, 0, stop.message)
    else:
        raise PasteOnly("paste_only", PASTE_ONLY)
    merged = merge.merge(engine, source, found, engine.now().timestamp(), complete=complete)
    return Read("ok", len(merged.new), len(merged.changed), len(merged.gone), None)


def _read(engine: Engine, source_id: str) -> Read:
    """Reads, merges, fills and records one source; the caller holds the lock."""
    global _reading
    conn = engine.db()
    source = store.get_source(conn, source_id)
    if source is None:
        raise LookupError(source_id)
    if source.reader == "paste":
        raise PasteOnly("paste_only", PASTE_ONLY)
    started = engine.now().timestamp()
    _reading = source_id
    try:
        try:
            read = _gather(engine, source)
        except Exception:
            log.exception("briefs: reading %s failed", source_id)
            read = Read("error", 0, 0, 0, FAILED)
        if read.result == "ok" or read.new:
            try:
                fill.fill(engine, store.briefs(conn, status=_LIVE))
            except Exception:
                log.exception("briefs: filling %s's briefs failed", source_id)
        with engine.lock:
            store.update_source(
                conn, source_id, last_run_at=started, last_result=read.result, error=read.error
            )
    finally:
        _reading = None
    engine.events.publish("brief", {"source_id": source_id, "result": read.result})
    return read


def read_source(engine: Engine, source_id: str) -> Read:
    """Reads one source now. Raises Busy while another read runs, PasteOnly for a paste-only source and
    LookupError for an unknown id."""
    if not _lock.acquire(blocking=False):
        raise Busy("busy", BUSY)
    try:
        return _read(engine, source_id)
    finally:
        _lock.release()


def _held(engine: Engine, source_id: str) -> None:
    """A Check now's read, in its own thread, with the lock its caller took."""
    global _reading
    try:
        _read(engine, source_id)
    except Exception:
        log.exception("briefs: the read of %s failed", source_id)
    finally:
        _reading = None
        _lock.release()


def check_now(engine: Engine, source_id: str) -> BriefSource:
    """Starts a read of one source in a thread of its own and returns the source. Raises LookupError for an
    unknown id, PasteOnly, TooSoon within an hour of its last read (or before the next morning's read once
    a site turned ClipForge away) and Busy while a read runs."""
    global _reading
    source = store.get_source(engine.db(), source_id)
    if source is None:
        raise LookupError(source_id)
    if source.reader == "paste":
        raise PasteOnly("paste_only", PASTE_ONLY)
    opens = check_now_after(engine, source)
    if opens is not None and source.last_result in _TURNED_AWAY:
        raise TooSoon("too_soon", TURNED_AWAY)
    if opens is not None:
        raise TooSoon("too_soon", TOO_SOON.format(time=f"{opens:%-I:%M %p}".lower()))
    if not _lock.acquire(blocking=False):
        raise Busy("busy", BUSY)
    _reading = source_id  # shown as reading from the moment it is asked for
    try:
        threading.Thread(target=_held, args=(engine, source_id), name="brief-read", daemon=True).start()
    except BaseException:
        _reading = None
        _lock.release()
        raise
    return source


def tick(engine: Engine) -> int:
    """The timer's look at the clock: reads each source that is due, in the list's order. How many it read;
    a Check now still running leaves the rest for the next tick."""
    now = engine.now()
    done = 0
    for source in store.sources(engine.db()):
        if not due(engine, source, now):
            continue
        try:
            read_source(engine, source.id)
        except Busy:
            break
        done += 1
    return done
