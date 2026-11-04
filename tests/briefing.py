"""Builders for the brief tests: a brief stored as a reader leaves it."""

from __future__ import annotations

from clipforge import repo
from clipforge.briefs import store
from clipforge.briefs.models import Brief


def add_brief(engine, **fields) -> Brief:
    """A brief from Campaign Scout on Whop, first seen at the engine's now, with ``fields`` over that."""
    now = engine.now().timestamp()
    brief_id = fields.pop("id", repo.new_id("br"))
    url = fields.pop("url", f"https://contentrewards.com/c/{brief_id}")
    made = Brief(
        id=brief_id,
        source_id=fields.pop("source_id", "bs_scout"),
        external_id=fields.pop("external_id", url or brief_id),
        url=url,
        marketplace=fields.pop("marketplace", "Whop"),
        title=fields.pop("title", "Clip the stream"),
        first_seen_at=now,
        last_seen_at=now,
        **fields,
    )
    return store.put_brief(engine.db(), made)
