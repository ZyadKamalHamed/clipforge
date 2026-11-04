"""Hashtag sets (SPEC 13): a niche, a name and up to ``MAX_TAGS`` hashtags to add to a post's caption in
one go. Each tag is kept without its ``#`` and once whatever its case, by the caption editor's rule for what
a tag can have (``edits.TAG_ONLY``); a tag with a space in it is refused rather than run together."""

from __future__ import annotations

from clipforge.edits import TAG_ONLY, EditError, _hashtags
from clipforge.untrusted import clean_text

MAX_TAGS = 30
NAME_CHARS = 60
NICHE_CHARS = 40  # as a niche's name in Settings
TOO_MANY = f"A set can have at most {MAX_TAGS} hashtags."
NO_TAGS = "Add at least one hashtag."
NO_NAME = "Give the set a name."
NO_NICHE = "Pick a niche for the set."
TOO_LONG = "Keep the {what} to {n} characters."


def _text(value: str, field: str, longest: int, empty: str) -> str:
    text = " ".join(clean_text(value).split())
    if not text:
        raise EditError(field, empty)
    if len(text) > longest:
        raise EditError(field, TOO_LONG.format(what=field, n=longest))
    return text


def clean_set(niche: str, name: str, tags: list[str]) -> tuple[str, str, list[str]]:
    """The set's niche, name and tags cleaned, or an ``EditError`` naming the field that is wrong."""
    niche = _text(niche, "niche", NICHE_CHARS, NO_NICHE)
    name = _text(name, "name", NAME_CHARS, NO_NAME)
    if any(len(tag.split()) > 1 for tag in tags):
        raise EditError("tags", TAG_ONLY)
    kept = [tag.removeprefix("#") for tag in _hashtags(tags, "tags")]
    if not kept:
        raise EditError("tags", NO_TAGS)
    if len(kept) > MAX_TAGS:
        raise EditError("tags", TOO_MANY)
    return niche, name, kept
