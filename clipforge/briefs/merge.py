"""Found briefs into the rows (SPEC 10.11). A new link is a new brief; a known one takes the new data and
keeps its status; a campaign that stopped, or that a complete read no longer lists, is gone, and one found
live again is new. A brief you used or hid keeps that. When a brief's text changes, what Claude filled is
dropped so the next fill reads it again."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from clipforge import repo
from clipforge.briefs import store
from clipforge.briefs.models import Brief, BriefIn, BriefSource
from clipforge.db import transaction

_LIVE = ["new", "seen"]
_KEPT = ("used", "hidden")  # statuses only you change
_FILLED = ("claude", "guess")
_SAME = {"id", "status", "first_seen_at", "last_seen_at"}  # left out when telling whether a brief changed


@dataclass(frozen=True)
class Merged:
    """The ids of the briefs this merge added (or found live again), changed and made gone."""

    new: list[str]
    changed: list[str]
    gone: list[str]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _data(item: BriefIn) -> tuple[dict[str, Any], dict[str, str]]:
    """The brief's fields from what the source found, and which of them it stated."""
    data = item.model_dump(exclude={"stated", "gone"})
    if data["platforms"] is None:
        del data["platforms"]  # the source didn't say, so both
    fields = {name: "stated" for name in item.stated if name in Brief.model_fields}
    return data, fields


def _keep_filled(data: dict[str, Any], fields: dict[str, str], known: Brief, item: BriefIn) -> None:
    """Keeps what Claude or the keyword guess filled on ``known`` for the fields ``item`` doesn't state."""
    for name, by in known.fields.items():
        if by not in _FILLED or name in item.stated:
            continue
        if name == "footage":
            data["footage"] = {"kind": item.footage.kind, "links": known.footage.links}
        else:
            data[name] = getattr(known, name)
        fields[name] = by


def merge(engine, source: BriefSource, found: list[BriefIn], now: float, *, complete: bool) -> Merged:
    """Writes ``found`` from ``source`` into the briefs at ``now``. ``complete`` means the read saw the
    source's whole list, so this source's live briefs it didn't find are gone."""
    new: list[str] = []
    changed: list[str] = []
    gone: list[str] = []
    seen: set[str] = set()
    conn = engine.db()
    with engine.lock, transaction(conn):
        for item in found:
            known = store.brief_by_key(conn, item.url, source.id, item.external_id)
            if known is not None and known.id in seen:
                continue
            data, fields = _data(item)
            digest = text_hash(item.text)
            if known is None:
                if item.gone:
                    continue  # stopped before it was ever seen
                brief = Brief(
                    id=repo.new_id("br"),
                    source_id=source.id,
                    fields=fields,
                    text_hash=digest,
                    status="new",
                    first_seen_at=now,
                    last_seen_at=now,
                    **data,
                )
                store.put_brief(conn, brief)
                new.append(brief.id)
                seen.add(brief.id)
                continue
            seen.add(known.id)
            if known.text_hash == digest:
                _keep_filled(data, fields, known, item)
            status = known.status
            if status not in _KEPT:
                if item.gone:
                    status = "gone"
                    if known.status != "gone":
                        gone.append(known.id)
                elif known.status == "gone":
                    status = "new"
                    new.append(known.id)
            brief = Brief(
                id=known.id,
                source_id=source.id,
                fields=fields,
                text_hash=digest,
                status=status,
                campaign_id=known.campaign_id,
                first_seen_at=known.first_seen_at,
                last_seen_at=now,
                **data,
            )
            store.put_brief(conn, brief)
            if known.id not in new and known.model_dump(exclude=_SAME) != brief.model_dump(exclude=_SAME):
                changed.append(known.id)
        if complete:
            for brief in store.briefs(conn, status=_LIVE, source_id=source.id):
                if brief.id not in seen:
                    store.update_brief(conn, brief.id, status="gone")
                    gone.append(brief.id)
    return Merged(new, changed, gone)
