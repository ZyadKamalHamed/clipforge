import logging
from pathlib import Path

from clipforge.asr.base import ASRResult, RawWord
from clipforge.asr.coverage import fill_from_captions, find_gaps, merge_words, transcribe_with_guard
from clipforge.asr.segment import build_transcript
from clipforge.errors import ToolError
from clipforge.ingest.youtube_signals import CapWord

WAV = Path("/not/read/by/the/fakes.wav")


def raw(start: float, length: float = 0.3, text: str = "w") -> RawWord:
    return RawWord(text=text, start=start, end=round(start + length, 6), conf=0.9)


def every(first: float, last: float, *, step: float = 0.5, text: str = "w") -> list[RawWord]:
    """Words 0.3 s long, starting every ``step`` seconds from ``first`` to ``last``."""
    count = round((last - first) / step) + 1
    return [raw(round(first + k * step, 6), text=text) for k in range(count)]


def caption_words(first: float = 0.0, last: float = 19.5) -> list[CapWord]:
    """Caption words every half second, each lasting until the next."""
    count = round((last - first) / 0.5) + 1
    return [CapWord(text="c", start=first + k * 0.5, end=first + (k + 1) * 0.5) for k in range(count)]


ASR = every(0.0, 9.5) + every(14.0, 19.5)  # the engine dropped everything from 9.8 s to 14 s
CAPTIONS = caption_words()


def result(words: list[RawWord]) -> ASRResult:
    return ASRResult(engine="parakeet", engine_version="test", language="en", words=words)


# Finding gaps


def test_find_gaps_detects_dropped_sentence():
    assert find_gaps(ASR, CAPTIONS, 20.0) == [(9.8, 14.0)]
    quiet = [c for c in CAPTIONS if not 9.8 < c.start < 14.0]  # a silence: the captions are empty there too
    assert find_gaps(ASR, quiet, 20.0) == []


def test_find_gaps_needs_three_caption_words():
    two = [c for c in CAPTIONS if not 10.5 < c.start < 14.0]  # only the words at 10 s and 10.5 s are left
    assert find_gaps(ASR, two, 20.0) == []
    three = [c for c in CAPTIONS if not 11.0 < c.start < 14.0]
    assert find_gaps(ASR, three, 20.0) == [(9.8, 14.0)]


def test_find_gaps_needs_more_than_two_seconds():
    assert find_gaps(every(0.0, 9.5) + every(11.8, 19.5), CAPTIONS, 20.0) == []  # 9.8 s to 11.8 s is 2 s


def test_find_gaps_before_the_first_word_and_after_the_last():
    assert find_gaps(every(5.0, 9.5), CAPTIONS, 20.0) == [(0.0, 5.0), (9.8, 20.0)]


def test_find_gaps_when_the_engine_heard_nothing():
    assert find_gaps([], CAPTIONS, 20.0) == [(0.0, 20.0)]
    assert find_gaps([], [], 20.0) == []


def test_a_long_word_covers_the_words_inside_it():
    words = [raw(0.0, 9.0, "looong"), raw(1.0)] + every(10.0, 19.5)
    assert find_gaps(words, CAPTIONS, 20.0) == []


# Merging and filling


def test_merge_words_replaces_window():
    # A word belongs to the window when its middle is inside it, so "five", which starts just inside but
    # mostly sits after it, stays, and its re-heard twin "fife" is not added.
    base = [raw(0.0, 1.0, "one"), raw(2.0, 1.0, "two"), raw(4.0, 1.0, "three"), raw(5.3, 0.7, "five")]
    patch = [raw(4.1, 0.9, "tree"), raw(2.1, 0.9, "too"), raw(5.4, 0.7, "fife"), raw(0.2, 0.5, "won")]
    merged = merge_words(base + [raw(6.5, 0.5, "six")], patch, (1.5, 5.5))
    assert [w.text for w in merged] == ["one", "too", "tree", "five", "six"]


def test_merge_words_leaves_out_a_kept_word_heard_again():
    # Heard again, the words either side of the gap can come back with times that put their middles inside
    # it. The same word overlapping a kept one is that word twice; a word said twice doesn't overlap.
    base = [raw(9.0, 1.0, "No,"), raw(10.0, 0.4, "players."), raw(14.0, 0.3, "go")]
    patch = [raw(10.1, 1.4, "players"), raw(10.5, 0.3, "no,"), raw(12.0, 0.3, "new"), raw(13.6, 0.5, "Go.")]
    merged = merge_words(base, patch, (10.4, 14.0))
    assert [w.text for w in merged] == ["No,", "players.", "no,", "new", "go"]


def test_fill_from_captions_leaves_out_a_caption_word_heard_already():
    words = [RawWord(text="say?", start=10.0, end=10.16), RawWord(text="Later.", start=20.0, end=20.5)]
    ref = [
        CapWord(text=text, start=start, end=end)
        for text, start, end in [("say?", 10.08, 13.04), ("what", 14.0, 14.3), ("now", 14.3, 14.6)]
    ]
    filled, ranges = fill_from_captions(words, ref, [(10.16, 16.0)])
    assert [w.text for w in filled] == ["say?", "what", "now", "Later."]
    assert ranges == [(10.16, 16.0)]
    alone, none = fill_from_captions(words, ref[:1], [(10.16, 16.0)])
    assert [w.text for w in alone] == ["say?", "Later."]
    assert none == []  # its only caption word was the one heard already, so nothing came from the captions


def test_fill_from_captions_marks_sentence_source():
    words = [RawWord(text="Before.", start=0.0, end=1.0), RawWord(text="After.", start=6.0, end=7.0)]
    ref = [
        CapWord(text=text, start=start, end=start + 0.5)
        for text, start in [("before", 0.0), ("the", 2.0), ("missing", 2.5), ("bit.", 3.0), ("after", 6.0)]
    ]
    filled, ranges = fill_from_captions(words, ref, [(1.0, 6.0), (7.0, 8.0)])
    assert [w.text for w in filled] == ["Before.", "the", "missing", "bit.", "After."]
    assert ranges == [(1.0, 6.0)]  # nothing in the captions for the second gap, so it isn't a range
    assert filled[1].conf is None
    transcript = build_transcript(result(filled), 8.0, ranges)
    assert [(s.text, s.source) for s in transcript.sentences] == [
        ("Before.", "asr"),
        ("the missing bit.", "captions"),
        ("After.", "asr"),
    ]


# The guard


class OneAtATime:
    """An engine that hears ``first`` on the full pass and the ``rerun`` words inside a window."""

    name = "parakeet"

    def __init__(self, first, rerun=(), *, fail=False):
        self.first, self.rerun, self.fail, self.calls = list(first), list(rerun), fail, []

    def available(self) -> bool:
        return True

    def heard(self, window) -> ASRResult:
        if self.fail:
            raise ToolError("asr_failed", "Speech to text failed.")
        return result([w for w in self.rerun if window[0] <= w.start < window[1]])

    def transcribe(self, wav, *, language=None, window=None, beam=False, timeout_s=None) -> ASRResult:
        self.calls.append((window, beam))
        return result(self.first) if window is None else self.heard(window)


class AllAtOnce(OneAtATime):
    """The same, with every window in one call, as the real engines do."""

    def transcribe_many(self, wav, windows, *, language=None, beam=False, timeout_s=None) -> list[ASRResult]:
        self.calls.append((list(windows), beam))
        return [self.heard(window) for window in windows]


def test_guard_reruns_the_gap_with_beam_search_and_merges():
    engine = AllAtOnce(ASR, rerun=every(7.0, 16.5, text="p"))
    fixed, ranges = transcribe_with_guard(engine, WAV, language="en", captions=CAPTIONS, duration=20.0)
    assert engine.calls == [(None, False), ([(6.8, 17.0)], True)]  # the gap padded by 3 s either side
    assert ranges == []
    assert [w.text for w in fixed.words].count("p") == 8 and len(fixed.words) == 40
    assert [w.start for w in fixed.words] == sorted(w.start for w in fixed.words)
    assert find_gaps(fixed.words, CAPTIONS, 20.0) == []


def test_guard_fills_from_captions_when_the_rerun_hears_nothing():
    fixed, ranges = transcribe_with_guard(
        AllAtOnce(ASR), WAV, language="en", captions=CAPTIONS, duration=20.0
    )
    assert ranges == [(9.8, 14.0)]
    assert [w.start for w in fixed.words if w.text == "c"] == [10.0 + k * 0.5 for k in range(8)]


def test_guard_without_captions_runs_once():
    for captions in (None, []):
        engine = AllAtOnce(ASR)
        fixed, ranges = transcribe_with_guard(engine, WAV, language="en", captions=captions, duration=20.0)
        assert engine.calls == [(None, False)] and ranges == [] and fixed.words == ASR


def test_guard_uses_the_captions_when_the_rerun_fails(caplog):
    engine = AllAtOnce(ASR, fail=True)
    with caplog.at_level(logging.WARNING):
        fixed, ranges = transcribe_with_guard(engine, WAV, language="en", captions=CAPTIONS, duration=20.0)
    assert ranges == [(9.8, 14.0)] and len(fixed.words) == 40
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_guard_pads_each_gap_but_stays_inside_the_video():
    engine = OneAtATime(every(2.5, 15.5))  # nothing heard in the first 2.5 s or the last 4.2 s
    transcribe_with_guard(engine, WAV, language=None, captions=CAPTIONS, duration=20.0)
    assert engine.calls == [(None, False), ((0.0, 5.5), True), ((12.8, 20.0), True)]
