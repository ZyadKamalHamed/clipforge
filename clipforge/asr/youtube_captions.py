"""YouTube's own caption words as the transcript, for a language no installed engine knows (SPEC 7.3).
Caption words carry no confidence."""

from __future__ import annotations

from clipforge.asr.base import ASRResult, RawWord, base_language
from clipforge.ingest.youtube_signals import CapWord


def transcript_from_captions(words: list[CapWord], track: str) -> ASRResult:
    """``track`` is the caption track's name (like "ar-orig"): the version, and its language."""
    return ASRResult(
        engine="youtube-captions",
        engine_version=track,
        language=base_language(track),
        words=[RawWord(text=w.text, start=w.start, end=w.end) for w in words],
    )
