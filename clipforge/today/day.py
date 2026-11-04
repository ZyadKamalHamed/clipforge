"""Today's list (SPEC 14.10, 15): a row a day for each thing that needs you, kept in step with it. An item
whose need goes is done by the app; one you tick stays ticked until something it didn't cover joins it. The
list runs by what each item could earn, the quicker first on a tie, and the lowest-value items that aren't
due are set aside as optional until the rest fit in 15 minutes. How long each kind of item takes is learnt
from your own timings, from the first click on Start to done."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import median
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from clipforge import repo
from clipforge.campaigns import tracker
from clipforge.today import path, store
from clipforge.today.changes import done_at
from clipforge.today.models import KINDS, Day, Need, Timing, TodoItem
from clipforge.today.needs import needs
from clipforge.today.value import comparable

if TYPE_CHECKING:
    from clipforge.app import Engine

BUDGET_S = 900  # the day's 15 minutes
# Seconds a draft, post, account, campaign or payout takes before your own timings say otherwise
DEFAULTS = {
    "drafts": 20,
    "submit": 15,
    "views": 10,
    "decide": 10,
    "ends": 10,
    "payout": 30,
    "needs_you": 60,
    "login": 60,
    "insights": 60,
    "exported": 60,
    "briefs": 30,
    "source": 60,
    "join": 90,
    "path": 60,
}
TIMINGS = 20  # the last timings an estimate goes on
FEWEST = 3  # timings before they count
LONGEST_S = 30 * 60  # an item open longer was left, not worked on
SHORTEST_S = 1.0
NEED_FIELDS = ("ids", "title", "detail", "value", "currency", "guess", "due", "campaigns")


def today_of(engine: Engine) -> date:
    """Today in the posting time zone."""
    return tracker.day_of(engine, engine.now())


def unit_seconds(engine: Engine, kind: str) -> float:
    """How long one of the kind's drafts or posts takes: the median of its last 20 timings once there are 3,
    else the default, never under a second."""
    timings = store.timings_of(engine.db(), kind, TIMINGS)
    if len(timings) < FEWEST:
        return float(DEFAULTS[kind])
    return max(SHORTEST_S, median(t.seconds / t.units for t in timings))


def _units(ids: list[str]) -> int:
    return max(1, len(ids))


def _fields(engine: Engine, need: Need) -> dict:
    """An item's fields as its need has them now, with its estimate: its own when it has one, else learnt."""
    learnt = need.estimate_s is None
    return {name: getattr(need, name) for name in NEED_FIELDS} | {
        "estimate_s": round(unit_seconds(engine, need.kind) * _units(need.ids))
        if learnt
        else need.estimate_s,
        "as_if_usd": comparable(engine.settings, need.value, need.currency)[1],
    }


def _finished(engine: Engine, item: TodoItem, how: str, now: float) -> dict:
    """The fields that mark the item done, with its timing written when it was started and not left."""
    if item.started_at is not None and now - item.started_at <= LONGEST_S:
        store.add_timing(
            engine.db(),
            Timing(
                id=repo.new_id("tm"),
                kind=item.kind,
                started_at=item.started_at,
                finished_at=now,
                seconds=max(0.0, now - item.started_at),
                units=item.units_at_start or _units(item.ids),
            ),
        )
    return {"done_at": now, "done_how": how, "done_ids": list(item.ids)}


REOPENED = {"done_at": None, "done_how": None, "done_ids": [], "started_at": None, "units_at_start": None}


def _reopens(item: TodoItem, need: Need) -> bool:
    """A done item opens again when its need is back after the app finished it, or, after your tick, when
    it has something the tick didn't cover."""
    if item.done_how == "app":
        return True
    return bool(set(need.ids) - set(item.done_ids))


def _sort_key(engine: Engine, item: TodoItem) -> tuple:
    worth = comparable(engine.settings, item.value, item.currency)[0]
    return (item.kind == "path", -worth, item.estimate_s, KINDS.index(item.kind), item.title)


def ordered(engine: Engine, items: list[TodoItem]) -> list[TodoItem]:
    """Undone first, by what each could earn, the quicker first on a tie, with the path's steps after the
    day's other work, as a step loses nothing by waiting a day; done after, newest first."""
    undone = sorted((i for i in items if not i.done), key=lambda i: _sort_key(engine, i))
    done = sorted((i for i in items if i.done), key=lambda i: i.done_at or 0.0, reverse=True)
    return undone + done


def _optional(engine: Engine, items: list[TodoItem]) -> set[str]:
    """The undone items set aside, the path's steps first, then lowest value first and never one that's
    due, until the day's other items fit in 15 minutes."""
    total = sum(i.estimate_s for i in items)
    aside: set[str] = set()
    for item in reversed(ordered(engine, [i for i in items if not i.done])):
        if total <= BUDGET_S:
            break
        if not item.due:
            aside.add(item.id)
            total -= item.estimate_s
    return aside


def _carried(engine: Engine, day: str) -> tuple[str | None, set[tuple[str, str]]]:
    """The last day before ``day`` with a list, and the kinds and refs it left undone."""
    before = store.last_days(engine.db(), day, 1)
    if not before:
        return None, set()
    last = before[0].day
    return last, {(i.kind, i.ref) for i in store.items_of(engine.db(), last) if not i.done}


def refresh(engine: Engine) -> list[TodoItem]:
    """Today's list kept in step with what needs you now, in order; ``todo`` is published when a row
    changed. An item whose need went is done when the change it waited on came, heard since the last
    refresh."""
    with engine.lock:
        conn = engine.db()
        today = today_of(engine)
        day = today.isoformat()
        now = engine.now().timestamp()
        heard = engine.changes.take()
        changed = False
        if store.get_day(conn, day) is None:
            store.put_day(conn, Day(day=day))
            changed = True
        found = {(n.kind, n.ref): n for n in needs(engine, today)}
        items = {(i.kind, i.ref): i for i in store.items_of(conn, day)}
        carried: tuple[str | None, set[tuple[str, str]]] | None = None
        for key, need in found.items():
            item = items.get(key)
            fields = _fields(engine, need)
            if item is None:
                if carried is None:
                    carried = _carried(engine, day)
                last, undone = carried
                items[key] = store.upsert_item(
                    conn,
                    TodoItem(
                        id=repo.new_id("td"),
                        day=day,
                        kind=need.kind,
                        ref=need.ref,
                        carried_from=last if key in undone else None,
                        created_at=now,
                        **fields,
                    ),
                )
                changed = True
                continue
            if item.done and not _reopens(item, need):
                continue
            if item.done:
                fields |= REOPENED
            if any(getattr(item, name) != value for name, value in fields.items()):
                items[key] = store.update_item(conn, item.id, **fields)
                changed = True
        for key, item in list(items.items()):
            if key in found or item.done:
                continue
            if item.kind == "path" and not path.on(engine):  # the guide turned off: its steps leave, undone
                store.delete_item(conn, item.id)
                del items[key]
            else:
                when = done_at(item.kind, item.started_at, heard, now)
                items[key] = store.update_item(conn, item.id, **_finished(engine, item, "app", when))
            changed = True
        aside = _optional(engine, list(items.values()))
        for key, item in items.items():
            if not item.done and item.optional != (item.id in aside):
                items[key] = store.update_item(conn, item.id, optional=item.id in aside)
                changed = True
        if changed:
            engine.events.publish("todo", {"day": day})
        return ordered(engine, list(items.values()))


def catch_up(engine: Engine) -> bool:
    """The list brought up to date at once after a change while an item's timer runs, so an item done on
    another page closes then; with no timer running the change is let go. Whether it refreshed."""
    if not engine.changes.pending():
        return False
    with engine.lock:
        timing = any(
            i.started_at is not None and not i.done for i in store.items_of(engine.db(), _day(engine))
        )
        if not timing:
            engine.changes.take()
            return False
        refresh(engine)
        return True


def _day(engine: Engine) -> str:
    return today_of(engine).isoformat()


def _item(engine: Engine, item_id: str) -> TodoItem:
    item = store.get_item(engine.db(), item_id)
    if item is None:
        raise LookupError("That to-do item has gone. The list was brought up to date.")
    return item


def start(engine: Engine, item_id: str) -> TodoItem:
    """The item with its timer running from the first click on Start; later clicks keep that time."""
    with engine.lock:
        item = _item(engine, item_id)
        if item.done or item.started_at is not None:
            return item
        item = store.update_item(
            engine.db(), item.id, started_at=engine.now().timestamp(), units_at_start=_units(item.ids)
        )
        engine.events.publish("todo", {"day": item.day})
        return item


def tick(engine: Engine, item_id: str) -> TodoItem:
    """The item done by your tick; one already done stays as it is. A step of the path is done for good."""
    with engine.lock:
        item = _item(engine, item_id)
        if item.done:
            return item
        if item.kind == "path" and item.ref in path.IDS:
            path.record(engine, item.ref, "hand")
        item = store.update_item(
            engine.db(), item.id, **_finished(engine, item, "hand", engine.now().timestamp())
        )
        engine.events.publish("todo", {"day": item.day})
        return item


def left_s(items: list[TodoItem]) -> int:
    """The estimates of the items still to do, leaving out the optional ones."""
    return sum(i.estimate_s for i in items if not i.done and not i.optional)


def used_s(engine: Engine, day: str) -> int:
    """The time your timed items took on ``day``, in whole seconds."""
    zone = ZoneInfo(engine.settings.posting.timezone)
    first = date.fromisoformat(day)
    start_at = datetime.combine(first, time(0), zone).timestamp()
    end_at = datetime.combine(first + timedelta(days=1), time(0), zone).timestamp()
    return round(sum(t.seconds for t in store.timings_between(engine.db(), start_at, end_at)))
