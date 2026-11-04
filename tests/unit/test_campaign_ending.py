"""When a campaign ends (SPEC 10.10): it is marked ended, its end date passed or its budget ran out. Its
clipping channels stop checking and say why, and its drafts and posts warn that they may not be paid."""

import time
from datetime import date

from clipforge import repo
from clipforge.campaigns.ending import campaign_state, end_reason
from clipforge.campaigns.models import Campaign


def campaign(**kw) -> Campaign:
    now = time.time()
    return Campaign(
        id=repo.new_id("cp"), name="Beast clips", marketplace="Whop", created_at=now, updated_at=now, **kw
    )


def test_end_reasons():
    today = date(2026, 10, 28)
    assert end_reason(None, today) is None
    assert end_reason(campaign(), today) is None
    assert end_reason(campaign(status="ended"), today) == "You marked Beast clips ended."
    assert end_reason(campaign(ends_on="2026-10-27"), today) == "Beast clips ended on 27 Oct."
    assert end_reason(campaign(ends_on="2026-10-28"), today) is None  # the last day itself is still on
    assert end_reason(campaign(budget_left=0), today) == "Beast clips has no budget left."
    assert end_reason(campaign(budget_left=0.5), today) is None
    both = campaign(status="ended", ends_on="2026-10-01", budget_left=0)
    assert end_reason(both, today) == "You marked Beast clips ended."  # what you said first


def test_campaign_state():
    today = date(2026, 11, 3)
    for last in ("2026-11-03", "2026-11-04", "2026-11-05"):
        assert campaign_state(campaign(ends_on=last), today) == "ending", last
    assert campaign_state(campaign(ends_on="2026-11-06"), today) == "active"
    assert campaign_state(campaign(ends_on="2026-11-02"), today) == "ended"
    assert campaign_state(campaign(status="ended", ends_on="2026-12-30"), today) == "ended"
    assert campaign_state(campaign(budget_left=0, ends_on="2026-11-04"), today) == "ended"
    assert campaign_state(campaign(), today) == "active"
