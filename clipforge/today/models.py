"""The day's to-do items, how long each kind took, and each day's plan (SPEC 14.10, 15). Days are dates in
the posting time zone, YYYY-MM-DD; times are epoch seconds; money is in each item's currency."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal[
    "drafts", "needs_you", "login", "insights", "submit", "views", "decide", "ends", "payout", "exported",
    "briefs", "source", "join", "path",
]  # fmt: skip
KINDS: tuple[str, ...] = Kind.__args__  # type: ignore[attr-defined]


class TodoItem(BaseModel):
    """One line of a day's list: what it is about (``kind`` and ``ref``, one row each a day), the ids it
    covers, what it could earn or protect, and whether it is done, seen done by ClipForge or ticked."""

    id: str
    day: str
    kind: Kind
    ref: str
    ids: list[str] = Field(default_factory=list)
    title: str
    detail: str | None = None
    estimate_s: int = Field(ge=0)
    value: float = 0.0
    currency: str = "USD"
    as_if_usd: bool = False
    guess: bool = False
    optional: bool = False
    due: bool = False
    campaigns: dict[str, int] = Field(default_factory=dict)
    carried_from: str | None = None
    started_at: float | None = None
    units_at_start: int | None = None
    done_at: float | None = None
    done_how: Literal["app", "hand"] | None = None
    done_ids: list[str] = Field(default_factory=list)
    created_at: float

    @property
    def done(self) -> bool:
        return self.done_at is not None


class Timing(BaseModel):
    """How long one item took from Start to done, and how many drafts or posts that covered."""

    id: str
    kind: Kind
    started_at: float
    finished_at: float
    seconds: float = Field(ge=0)
    units: int = Field(ge=1)


class Day(BaseModel):
    """A day's plan: the drafts cap it was built with, the minutes it planned and used, and when the morning
    notification went."""

    day: str
    drafts_cap: int | None = None
    planned_s: int | None = None
    used_s: int = 0
    notified_at: float | None = None


@dataclass
class Need:
    """Something that needs you now, before it has a row on the day's list."""

    kind: str
    ref: str
    ids: list[str]
    title: str
    detail: str | None = None
    value: float = 0.0
    currency: str = "USD"
    guess: bool = False
    due: bool = False
    campaigns: dict[str, int] = field(default_factory=dict)
    estimate_s: int | None = None  # its own estimate, for one that isn't learnt from your timings


@dataclass(frozen=True)
class Estimate:
    """What something could earn, in ``currency``; None when there is no rate to work it out from."""

    amount: float | None
    currency: str = "USD"
    guess: bool = False
