"""Text helpers the brain's passes share: comparing words whatever their case, accents or script, and
tidying what the LLM wrote."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

QUOTE_MARKS = "\"'\u201c\u201d\u2018\u2019"
# Scripts written without spaces between words (Thai, Lao, Myanmar, Khmer, Japanese, Chinese): each
# character counts as a word when quotes are compared.
UNSPACED = re.compile(
    "([\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])"
)
NOT_WORD = re.compile(r"[\W_]+")


def plain_words(text: str) -> str:
    """Text as lowercase words and single spaces, for finding what the LLM quoted in the transcript."""
    text = UNSPACED.sub(r" \1 ", unicodedata.normalize("NFKC", text).casefold())
    return " ".join(NOT_WORD.sub(" ", text).split())


def cleaned(values: list[str], clean: Callable[[str], str]) -> list[str]:
    """Each value cleaned and its spaces collapsed, leaving out empty ones and repeats whatever their case."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = " ".join(clean(value).split())
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            out.append(value)
    return out


def shortened(text: str, chars: int, words: int | None = None) -> str:
    """The text cut at a word boundary to at most ``words`` words and ``chars`` characters, its spaces
    collapsed; a first word longer than that is cut at ``chars``."""
    parts = text.split()[:words]
    if not parts:
        return ""
    out = parts[0][:chars]
    for part in parts[1:]:
        if len(out) + 1 + len(part) > chars:
            break
        out = f"{out} {part}"
    return out


def title_word(word: str | None, title: str) -> str | None:
    """The word of ``title`` that ``word`` names, whatever its case and punctuation, or None."""
    key = plain_words(word or "")
    if not key:
        return None
    return next((token for token in title.split() if plain_words(token) == key), None)


def unquoted(title: str) -> str:
    """The title without the quote marks around it; an apostrophe that belongs to a word stays."""
    title = title.strip()
    while len(title) >= 2 and title[0] in QUOTE_MARKS and title[-1] in QUOTE_MARKS:
        title = title[1:-1].strip()
    return title
