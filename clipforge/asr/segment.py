"""Words into a transcript (SPEC 7.3): fillers marked, the pause after every word, and sentences that
end where the speaker's sentence ends and never run past 30 words or 12 s, so a clip can start and stop
on a sentence and captions stay readable. A sentence that carries on the one before is marked, so a clip
keeps the whole thought."""

from __future__ import annotations

from clipforge.asr.base import ASRResult, base_language
from clipforge.models import Sentence, Transcript, Word

FILLERS = {"um", "uh", "erm", "er", "ah", "hmm"}
ELLIPSIS = chr(0x2026)
# Sentence ends, also for languages only Whisper knows: the full stops and marks of Chinese and
# Japanese, the Arabic question mark and the Hindi danda.
TERMINAL = (".", "?", "!", ELLIPSIS, chr(0x3002), chr(0xFF1F), chr(0xFF01), chr(0x061F), chr(0x0964))
CLOSERS = "\"')]}" + chr(0x201D) + chr(0x201C) + chr(0x2019) + chr(0x00BB)  # quotes and brackets after a mark
CLAUSE = (",", ";", ":")
# Words whose full stop rarely ends a sentence: "Dr. Smith", "e.g. this".
ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "st", "mt", "jr", "sr", "vs", "eg", "ie"}
# English sentences that open on these carry on the one before ("I had an epiphany. That I don't think
# anyone is lazy."). "That" does only before a subject, so "That was it." starts a thought of its own.
CARRY_ON = {"which", "because", "except"}
SUBJECTS = {"i", "you", "he", "she", "we", "they", "it", "there"}
PUNCT_EVERY = 60  # punctuation is trusted with at least one sentence end per this many words
SPLIT_PAUSE_S = 0.25  # a long sentence splits at its longest pause only when that pause is longer
EPS = 1e-6
# Common English words that are not common words in any other language Parakeet knows, for telling
# whether a transcript with no language given is English.
ENGLISH_HINTS = {
    "the", "and", "you", "that", "this", "what", "with", "they", "it", "but", "not", "your", "there",
    "about", "because", "know", "think", "really", "yeah", "like", "would", "could", "people", "going",
    "want", "she", "dont", "its", "thats", "youre", "whats",
}  # fmt: skip
ENGLISH_SHARE = 0.1


def _letters(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalpha())


def _squeeze(letters: str) -> str:
    """Runs of one letter as one: "ummm" is "um"."""
    return "".join(c for k, c in enumerate(letters) if k == 0 or c != letters[k - 1])


SQUEEZED_FILLERS = {_squeeze(f) for f in FILLERS}


def is_filler(text: str) -> bool:
    """Um, uh, erm, er, ah and hmm, however long they are drawn out. A word in capitals is an acronym
    ("the ER"), never a filler."""
    letters = "".join(c for c in text if c.isalpha())
    if len(letters) > 1 and letters.isupper():
        return False
    return bool(letters) and _squeeze(letters.lower()) in SQUEEZED_FILLERS


def looks_english(words: list[Word]) -> bool:
    hints = sum(1 for w in words if _letters(w.text) in ENGLISH_HINTS)
    return bool(words) and hints >= ENGLISH_SHARE * len(words)


def is_english(words: list[Word], language: str | None) -> bool:
    """Whether the words are English: the language says, or with none given, the words themselves do."""
    code = base_language(language)
    return looks_english(words) if code is None else code == "en"


def mark_fillers(words: list[Word], language: str | None = None) -> list[Word]:
    """Copies of the words with ``filler`` set. Only English has these fillers: "um" and "er" are words
    in Portuguese, German or Danish. With no language given, the words themselves decide."""
    english = is_english(words, language)
    return [w.model_copy(update={"filler": english and is_filler(w.text)}) for w in words]


def compute_pauses(words: list[Word]) -> list[Word]:
    """Copies of the words with the silence after each one; overlapping words and the last word get 0."""
    return [
        w.model_copy(
            update={"pause_after": max(0.0, words[k + 1].start - w.end) if k + 1 < len(words) else 0.0}
        )
        for k, w in enumerate(words)
    ]


def continues(text: str) -> bool:
    """Whether an English sentence carries on the one before it: it opens on "which", "because" or
    "except", or on "that" and a subject, and it isn't a question."""
    words = text.split()
    if not words or text.rstrip(CLOSERS).endswith("?"):
        return False
    first = _letters(words[0])
    if first in CARRY_ON:
        return True
    if first != "that" or words[0].rstrip(CLOSERS).endswith(CLAUSE) or len(words) < 2:
        return False
    subject = words[1].replace(chr(0x2019), "'").split("'")[0]  # "it's" is "it"
    return _letters(subject) in SUBJECTS


def _ends_sentence(text: str) -> bool:
    core = text.rstrip(CLOSERS)
    if not core.endswith(TERMINAL):
        return False
    if core.endswith(".") and not core.endswith(".."):
        return _letters(core) not in ABBREVIATIONS
    return True


def _too_long(words: list[Word], a: int, b: int, max_words: int, max_s: float) -> bool:
    end = max(w.end for w in words[a : b + 1])
    return b - a + 1 > max_words or end - words[a].start > max_s + EPS


def _split_point(words: list[Word], gaps: list[float], a: int, b: int) -> int:
    """The last word of the first piece when words a to b split in two: after the longest pause when one
    is over 0.25 s, else after the comma nearest the middle, else after the middle word."""
    n = b - a + 1
    middle = a + n / 2

    def nearest(candidates: list[int]) -> int:
        return min(candidates, key=lambda k: (abs(k + 1 - middle), k))

    longest = max(gaps[a:b])
    if longest > SPLIT_PAUSE_S + EPS:
        return nearest([k for k in range(a, b) if gaps[k] >= longest - EPS])
    commas = [k for k in range(a, b) if words[k].text.rstrip(CLOSERS).endswith(CLAUSE)]
    if commas:
        return nearest(commas)
    return a + n // 2 - 1


def _fit(
    words: list[Word], gaps: list[float], span: tuple[int, int], max_words: int, max_s: float
) -> list[tuple[int, int]]:
    """The span cut in two until every piece fits; a single word always fits."""
    out, stack = [], [span]
    while stack:
        a, b = stack.pop()
        if a < b and _too_long(words, a, b, max_words, max_s):
            k = _split_point(words, gaps, a, b)
            stack += [(k + 1, b), (a, k)]
        else:
            out.append((a, b))
    return out


def segment(
    words: list[Word],
    *,
    has_punct: bool | None = None,
    max_words: int = 30,
    max_s: float = 12.0,
    pause_split_s: float = 0.6,
    english: bool = False,
) -> list[Sentence]:
    """Sentences numbered S1 on. With punctuation a sentence ends at a word ending in a full stop, a
    question mark, an exclamation mark or an ellipsis; without it (or with too little to trust) at a
    pause of ``pause_split_s`` or more. Then every sentence too long is split until it fits. A sentence
    carries on the one before (``cont``) when it is a later piece of a split one or, when the words are
    ``english``, when it opens like a part of the one before (``continues``)."""
    if not words:
        return []
    n = len(words)
    gaps = [words[k + 1].start - words[k].end for k in range(n - 1)]
    if has_punct is None:
        marks = sum(1 for w in words if _ends_sentence(w.text))
        has_punct = marks > 0 and marks * PUNCT_EVERY >= n
    spans, a = [], 0
    for k in range(n):
        if k == n - 1 or (_ends_sentence(words[k].text) if has_punct else gaps[k] >= pause_split_s - EPS):
            spans.append((a, k))
            a = k + 1
    pieces = [
        (piece, j > 0) for span in spans for j, piece in enumerate(_fit(words, gaps, span, max_words, max_s))
    ]
    sentences = []
    for k, ((a, b), split) in enumerate(pieces):
        text = " ".join(w.text for w in words[a : b + 1])
        sentences.append(
            Sentence(
                id=f"S{k + 1}",
                start=words[a].start,
                end=max(w.end for w in words[a : b + 1]),
                w0=words[a].i,
                w1=words[b].i,
                text=text,
                cont=split or (english and k > 0 and continues(text)),
            )
        )
    return sentences


def build_transcript(
    result: ASRResult, duration: float, caption_ranges: list[tuple[float, float]] | tuple = ()
) -> Transcript:
    """The transcript of a whole video from an engine's words: empty words dropped, every word kept
    inside the video and in time order, fillers, pauses and the sentences that carry on another marked,
    and the sentences that overlap a stretch filled from YouTube's captions marked as such."""
    kept = []
    for w in result.words:
        text = w.text.strip()
        if text:
            start = min(max(w.start, 0.0), duration)
            kept.append((start, min(max(w.end, start), duration), text, w.conf))
    kept.sort(key=lambda word: word[0])
    words = [Word(i=i, text=text, start=s, end=e, conf=conf) for i, (s, e, text, conf) in enumerate(kept)]
    words = compute_pauses(mark_fillers(words, result.language))
    english = is_english(words, result.language)
    sentences = [
        s.model_copy(update={"source": "captions"})
        if any(s.start < b and s.end > a for a, b in caption_ranges)
        else s
        for s in segment(words, english=english)
    ]
    return Transcript(
        engine=result.engine,
        engine_version=result.engine_version,
        language=result.language,
        duration=duration,
        words=words,
        sentences=sentences,
    )
