"""Wording (SPEC 8.3, 8.4 and 9.2): the limits a hook title and hashtags keep whichever pass wrote them,
the credit line, and the text posted under a clip."""

from __future__ import annotations

import unicodedata

from clipforge.brain.text import plain_words, shortened
from clipforge.models import ProjectOptions, SourceMeta

TITLE_WORDS = (3, 8)
TITLE_CHARS = 42
TITLE_END = ".,;:… "  # left off the end of a cut title; ? and ! stay
# A title cut short doesn't end on one of these: it would leave the phrase hanging ("dormant most of").
DANGLING = {
    "a", "about", "all", "although", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been",
    "between", "but", "by", "can", "could", "did", "do", "does", "each", "even", "every", "few", "for",
    "from", "had", "has", "have", "his", "i", "if", "in", "into", "is", "its", "just", "like", "many", "may",
    "might", "more", "most", "much", "must", "my", "nor", "of", "on", "onto", "or", "our", "per", "really",
    "several", "shall", "should", "since", "so", "some", "than", "the", "their", "though", "through", "to",
    "toward", "towards", "unless", "until", "upon", "very", "via", "was", "were", "when", "where", "whether",
    "which", "while", "whose", "will", "with", "without", "would", "your",
}  # fmt: skip
TIKTOK_TAGS = 4
INSTAGRAM_TAGS = 5
TAG_CHARS = 40  # longer isn't a topic tag, and five of these always leave room for a caption
POST_CHARS = 2200  # in UTF-16 units, as TikTok counts them; Instagram's count is no stricter


def hashtags(tags: list[str], limit: int) -> list[str]:
    """Each tag as ``#`` and lowercase letters, digits and underscores, in any script (accents and marks
    stay). A tag without a letter, past 40 characters or given already is left out; at most ``limit``."""
    out: list[str] = []
    for tag in tags:
        text = unicodedata.normalize("NFKC", tag).lower()
        word = "".join(ch for ch in text if ch == "_" or unicodedata.category(ch)[0] in "LNM")
        if any(ch.isalpha() for ch in word) and len(word) <= TAG_CHARS and f"#{word}" not in out:
            out.append(f"#{word}")
    return out[:limit]


def credit_line(meta: SourceMeta, rights_basis: str, options: ProjectOptions) -> str | None:
    """``Source: <channel>`` for the post caption (SPEC 8.4), when the video gets a credit."""
    channel = credit_channel(meta, rights_basis, options)
    return f"Source: {channel}" if channel else None


def credit_channel(meta: SourceMeta, rights_basis: str, options: ProjectOptions) -> str | None:
    """The channel to credit: on unless the video is from the user's own channel, and the project's credit
    option turns it on or off whatever the rights. None without a channel."""
    on = options.credit if options.credit is not None else rights_basis != "own"
    channel = " ".join((meta.channel or "").split())
    return channel if on and channel else None


def post_text(caption: str, tags: list[str], credit: str | None) -> str:
    """The text posted under the clip: its caption, the credit line and the hashtags, a blank line apart."""
    return "\n\n".join(part for part in (caption, credit, " ".join(tags)) if part)


def units(text: str) -> int:
    """The length TikTok and Instagram count: UTF-16 code units, so an emoji counts 2."""
    return len(text.encode("utf-16-le")) // 2


def cut_title(text: str) -> str:
    """The title cut at a word boundary to at most 8 words and 42 characters, with no full stop, comma,
    colon or ellipsis left at the end (? and ! stay). When words were cut, any that would leave it
    hanging ("of", "the", "and", "is" and the like) come off the end too, down to 3 words."""
    words = shortened(text, TITLE_CHARS, TITLE_WORDS[1]).split()
    if len(words) < len(text.split()):
        while len(words) > TITLE_WORDS[0] and plain_words(words[-1]) in DANGLING:
            words.pop()
    return " ".join(words).rstrip(TITLE_END)
