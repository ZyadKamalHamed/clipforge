"""When a linked campaign ends (SPEC 10.10): you marked it ended, its end date passed or its budget ran out.
Its clipping channels stop checking and say why, and its posts warn that they may not be paid."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from clipforge.campaigns.models import Campaign

ENDING_DAYS = 3  # a campaign this close to its last day shows as ending on the sheet


def end_reason(campaign: Campaign | None, today: date) -> str | None:
    """Why the campaign is over, as a sentence, or None while it runs. Its end date is its last day, and
    ``today`` is the posting time zone's."""
    if campaign is None:
        return None
    if campaign.status == "ended":
        return f"You marked {campaign.name} ended."
    if campaign.ends_on is not None:
        last = date.fromisoformat(campaign.ends_on)
        if last < today:
            return f"{campaign.name} ended on {last.day} {last:%b}."
    if campaign.budget_left == 0:
        return f"{campaign.name} has no budget left."
    return None


def campaign_state(campaign: Campaign, today: date) -> Literal["active", "ending", "ended"]:
    """The campaign's state on the sheet: "ended" once it has a reason to be over, "ending" while its last day
    is less than ENDING_DAYS away (today counts), else "active"."""
    if end_reason(campaign, today) is not None:
        return "ended"
    if (
        campaign.ends_on is not None
        and 0 <= (date.fromisoformat(campaign.ends_on) - today).days < ENDING_DAYS
    ):
        return "ending"
    return "active"
