"""Hashtag sets (SPEC 13): a niche, a name and up to 30 tags, each kept without its # and once whatever its
case, with the caption editor's rule for what a tag can have."""

import pytest

from clipforge.edits import TAG_ONLY, EditError
from clipforge.trends import hashtags


def test_hashtag_sets_are_cleaned():
    assert hashtags.clean_set(" Gaming ", "  Clips ", ["#Gaming", "gaming", "  ", "#fyp"]) == (
        "Gaming",
        "Clips",
        ["Gaming", "fyp"],
    )
    for tags in (["bad tag"], ["#fy-p"], ["ok", "no!"]):
        with pytest.raises(EditError) as err:
            hashtags.clean_set("Gaming", "Clips", tags)
        assert (err.value.field, err.value.message) == ("tags", TAG_ONLY), tags


def test_hashtag_sets_have_limits():
    assert hashtags.MAX_TAGS == 30
    many = [f"tag{n}" for n in range(30)]
    assert hashtags.clean_set("Gaming", "Clips", many)[2] == many
    for niche, name, tags, field, sentence in (
        ("Gaming", "Clips", [*many, "one_more"], "tags", hashtags.TOO_MANY),
        ("Gaming", "Clips", [], "tags", hashtags.NO_TAGS),
        ("Gaming", "  ", ["fyp"], "name", hashtags.NO_NAME),
        ("Gaming", "x" * 61, ["fyp"], "name", "Keep the name to 60 characters."),
        ("", "Clips", ["fyp"], "niche", hashtags.NO_NICHE),
        ("x" * 41, "Clips", ["fyp"], "niche", "Keep the niche to 40 characters."),
    ):
        with pytest.raises(EditError) as err:
            hashtags.clean_set(niche, name, tags)
        assert (err.value.field, err.value.message) == (field, sentence), (niche, name, len(tags))
