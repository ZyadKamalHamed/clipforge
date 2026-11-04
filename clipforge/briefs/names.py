"""Name ideas, a bio and a profile picture idea for an account worth adding (SPEC 10.11). They come from
fixed templates, never from Claude or a brief's words, so they can't take a show's, network's, creator's or
brand's name: each idea says it is clips, and the bio says it is a fan clips page."""

from __future__ import annotations

import re

from clipforge.config import Niche

LABEL_MAX = 16
NO_LOGO = "with no logo or anyone's photo"
# the 12 default niches (config.DEFAULT_NICHES), by name in lower case: a label and a plain icon idea
_DEFAULTS = {
    "reality tv and drama": ("Drama", "a bold speech bubble on black"),
    "gaming": ("Gaming", "a game controller outline"),
    "streamers": ("Streamer", "a glowing live dot on a dark screen"),
    "podcasts and interviews": ("Podcast", "a microphone outline"),
    "sports": ("Sports", "a ball with motion lines"),
    "comedy and entertainment": ("Comedy", "a laughing face drawn in a few lines"),
    "music": ("Music", "a pair of headphones"),
    "finance and business": ("Money", "a stack of coins"),
    "tech": ("Tech", "a chip outline"),
    "lifestyle and fitness": ("Fitness", "a dumbbell outline"),
    "education": ("Learning", "an open book"),
    "news and politics": ("News", "a folded newspaper"),
}
_WORD = re.compile(r"\W+")


def label(niche: Niche) -> str:
    """The short word the ideas start with: a default niche's own, else the niche's first word without its
    punctuation, at most 16 letters, with a capital first letter ("Fan" when no word has a letter)."""
    known = _DEFAULTS.get(niche.name.casefold())
    if known is not None:
        return known[0]
    words = [w for w in (_WORD.sub("", w) for w in niche.name.split()) if w]
    first = words[0][:LABEL_MAX] if words else "Fan"
    return first[0].upper() + first[1:]


def ideas(niche: Niche) -> list[str]:
    """Three name ideas in the style of "Drama TV Clips"."""
    name = label(niche)
    return [f"{name} TV Clips", f"{name} Clips Daily", f"{name} Clip Vault"]


def bio(niche: Niche) -> str:
    return f"Daily {niche.name.lower()} clips. A fan clips page, not affiliated with anyone shown."


def picture(niche: Niche) -> str:
    """A profile picture idea: a plain icon, never a logo or a person."""
    known = _DEFAULTS.get(niche.name.casefold())
    if known is not None:
        return f"{known[1]}, {NO_LOGO}"
    return f"a simple {label(niche).lower()} icon in your accent colour, {NO_LOGO}"
