import numpy as np
import pytest

from clipforge.brain.signals import apply_tags, compute_signals, covered, range_signals
from clipforge.ingest.youtube_signals import CapEvent, Hotspot
from clipforge.models import Chapter, HeatBin, Sentence, Transcript, Word


def transcript(*spans: tuple[float, float], words_each: int = 4) -> Transcript:
    """Sentences S1 on over the given (start, end) spans, each with ``words_each`` evenly spread words."""
    words, sentences = [], []
    for k, (start, end) in enumerate(spans):
        w0, step = len(words), (end - start) / words_each
        for j in range(words_each):
            words.append(Word(i=len(words), text=f"w{j}", start=start + j * step, end=start + (j + 1) * step))
        text = " ".join(w.text for w in words[w0:])
        sentences.append(Sentence(id=f"S{k + 1}", start=start, end=end, w0=w0, w1=len(words) - 1, text=text))
    duration = max(end for _, end in spans)
    return Transcript(
        engine="parakeet",
        engine_version="test",
        language="en",
        duration=duration,
        words=words,
        sentences=sentences,
    )


def seconds(n: int) -> Transcript:
    """n sentences, one per second."""
    return transcript(*[(float(k), k + 1.0) for k in range(n)])


def heat(values: list[float]) -> list[HeatBin]:
    return [HeatBin(start=float(k), end=k + 1.0, value=v) for k, v in enumerate(values)]


def signals(tr: Transcript, rms=None, **kwargs):
    args = {"heatmap": [], "events": [], "hotspots": [], "chapters": []} | kwargs
    rms = np.ones(int(tr.duration) + 1) if rms is None else np.asarray(rms, dtype=float)
    return compute_signals(tr, rms, **args)


def tags(tr: Transcript, sig, chapters=()) -> list[list[str]]:
    return [s.tags for s in apply_tags(tr, sig, list(chapters)).sentences]


# Energy


def test_covered_seconds():
    assert covered(0.0, 5.0) == slice(0, 5)  # ending right on second 5 doesn't cover it
    assert covered(7.2, 7.2) == slice(7, 8)  # a moment covers the second it falls in
    assert covered(4.9999999, 8.0) == slice(5, 8)  # a start a hair before second 5 is float noise
    assert covered(0.0, 5.0000001) == slice(0, 5)  # and so is an end a hair after it
    assert covered(-0.5, 3.0) == slice(0, 3)  # nothing comes before second 0


def test_energy_tags():
    tr = seconds(10)
    sig = signals(tr, np.linspace(0.1, 1.0, 10))
    assert [sig.sentences[s.id].energy_pct for s in tr.sentences] == pytest.approx([k / 9 for k in range(10)])
    assert tags(tr, sig) == [["{subdued}"]] * 2 + [[]] * 6 + [["{energetic}"]] * 2
    assert all(s.tags == [] for s in tr.sentences)  # apply_tags returns a copy
    few = seconds(7)
    assert tags(few, signals(few, np.linspace(0.1, 1.0, 7))) == [[]] * 7  # too few sentences to rank
    eight = seconds(8)  # the fewest that are ranked
    assert tags(eight, signals(eight, np.linspace(0.1, 1.0, 8)))[0] == ["{subdued}"]
    many = seconds(21)  # ranks in steps of 0.05, so some sentences sit exactly on 0.15 and 0.85
    expected = [["{subdued}"]] * 4 + [[]] * 13 + [["{energetic}"]] * 4
    assert tags(many, signals(many, np.linspace(0.1, 1.0, 21))) == expected


def test_sentence_energy_is_the_mean_of_the_seconds_it_covers():
    # S1 ends on a second's boundary, so it covers second 0 only; S2 covers seconds 1 and 2.
    tr = transcript((0.0, 1.0), (1.2, 2.5))
    pct = lambda sig: [sig.sentences[s].energy_pct for s in ("S1", "S2")]  # noqa: E731
    assert pct(signals(tr, [0.4, 0.9, 0.0])) == [0.0, 1.0]  # 0.4 against 0.45
    assert pct(signals(tr, [0.5, 0.9, 0.0])) == [1.0, 0.0]  # 0.5 against 0.45
    moment = transcript((0.0, 1.0), (2.0, 2.0))  # a sentence with no length still has the second it is in
    assert pct(signals(moment, [0.4, 0.0, 0.9])) == [0.0, 1.0]
    late = transcript((0.0, 1.0), (5.0, 6.0))  # S2 is past the end of the audio, so it is silent
    assert pct(signals(late, [0.4])) == [1.0, 0.0]


# Audience replay


def test_replayed_tag():
    tr = seconds(10)
    bins = heat([0.1 * (k + 1) for k in range(10)])
    sig = signals(tr, heatmap=bins)
    assert sig.has_heatmap and sig.heat_p80 == pytest.approx(0.82)
    assert [k for k, t in enumerate(tags(tr, sig)) if "[replayed]" in t] == [8, 9]
    assert (sig.sentences["S1"].heat_pct, sig.sentences["S10"].heat_pct) == (0.0, 1.0)
    assert signals(tr, heatmap=bins[::-1]).sentences == sig.sentences  # the bins' order doesn't matter
    across = transcript((0.0, 1.0), (6.5, 8.2))  # bins 6, 7 and 8: the highest counts
    assert signals(across, heatmap=bins).sentences["S2"].heat_pct == pytest.approx(8 / 9)
    moment = transcript((0.0, 1.0), (5.0, 5.0))  # no length, right where bin 5 starts
    assert signals(moment, heatmap=bins).sentences["S2"].heat_pct == pytest.approx(5 / 9)
    eleven = seconds(11)
    at_p80 = signals(eleven, heatmap=heat([float(k) for k in range(11)]))  # bin 8 sits on the 80th percentile
    assert at_p80.heat_p80 == 8.0
    assert [k for k, t in enumerate(tags(eleven, at_p80)) if "[replayed]" in t] == [8, 9, 10]
    nine = seconds(9)
    sig = signals(nine, heatmap=bins[:9])
    assert sig.sentences["S9"].heat_pct == 1.0 and all("[replayed]" not in t for t in tags(nine, sig))


def test_signals_without_heatmap_or_comments():
    tr = seconds(12)
    sig = signals(tr, np.linspace(0.0, 1.0, 12))
    assert not (sig.has_heatmap or sig.has_comments or sig.has_chat or sig.has_events)
    assert sig.heat_p80 is None and sig.max_bucket_weight == 0.0
    assert all(s.heat_pct is None for s in sig.sentences.values())
    every = [t for sentence_tags in tags(tr, sig) for t in sentence_tags]
    assert not any(t == "[replayed]" or t.startswith("[comments") for t in every)
    assert len(sig.rms) == 12


# Comments, events, chat and chapters


def test_comments_tag():
    tr = transcript((16.0, 20.0), (28.0, 33.0), (35.0, 40.0))
    hotspots = [
        Hotspot(start=15.0, end=30.0, count=4, weight=9.0, hot=True),
        Hotspot(start=30.0, end=45.0, count=1, weight=1.0, hot=False),
    ]
    sig = signals(tr, hotspots=hotspots)
    got = [(sig.sentences[s.id].comments_n, sig.sentences[s.id].comments_w) for s in tr.sentences]
    assert got == [(4, 9.0), (4, 10.0), (0, 1.0)]
    assert tags(tr, sig) == [["[comments: 4]"], ["[comments: 4]"], []]
    assert sig.has_comments and sig.max_bucket_weight == 9.0
    lone = signals(tr, hotspots=hotspots[1:])
    assert not lone.has_comments and lone.max_bucket_weight == 1.0  # one lone mention is no hotspot
    two_hot = [
        Hotspot(start=0.0, end=15.0, count=2, weight=4.0, hot=True),
        Hotspot(start=15.0, end=30.0, count=5, weight=9.0, hot=True),
    ]
    across = signals(transcript((14.0, 16.0)), hotspots=two_hot).sentences["S1"]
    assert (across.comments_n, across.comments_w) == (5, 13.0)  # the biggest count, every weight


def test_laughter_halo_two_seconds():
    tr = transcript((10.0, 12.0), (30.0, 32.0), (50.0, 52.0), (70.0, 72.0), (90.0, 92.0))
    events = [
        CapEvent(kind="laughter", start=13.5, end=14.0),  # 1.5 s after S1 ends
        CapEvent(kind="applause", start=34.5, end=35.0),  # 2.5 s after S2 ends: too late
        CapEvent(kind="music", start=46.0, end=48.5),  # ends 1.5 s before S3 starts
        CapEvent(kind="applause", start=74.0, end=74.5),  # exactly 2 s after S4 ends: the halo is closed
        CapEvent(kind="laughter", start=87.0, end=88.0),  # ends exactly 2 s before S5 starts
    ]
    sig = signals(tr, events=events)
    got = [sig.sentences[s.id].events for s in tr.sentences]
    assert got == [["laughter"], [], ["music"], ["applause"], ["laughter"]]
    assert tags(tr, sig) == [["[Laughter]"], [], ["[Music]"], ["[Applause]"], ["[Laughter]"]]
    assert sig.has_events


def test_a_long_event_does_not_hide_the_ones_inside_it():
    tr = transcript((10.0, 12.0), (30.0, 32.0))
    events = [CapEvent(kind="music", start=0.0, end=40.0), CapEvent(kind="laughter", start=11.0, end=11.5)]
    sig = signals(tr, events=events)
    assert [sig.sentences[s.id].events for s in tr.sentences] == [["laughter", "music"], ["music"]]


def test_chat_burst_tag():
    tr = transcript((10.0, 12.0), (21.0, 23.0), (40.0, 42.0))
    sig = signals(tr, chat_bursts=[(20.0, 30.0)])
    assert sig.has_chat and [sig.sentences[s.id].chat_burst for s in tr.sentences] == [False, True, False]
    assert tags(tr, sig) == [[], ["[chat burst]"], []]


def test_tags_come_in_one_order():
    tr = seconds(10)
    bins = heat([0.1 * (k + 1) for k in range(10)])
    hotspots = [Hotspot(start=9.0, end=10.0, count=2, weight=5.0, hot=True)]
    events = [CapEvent(kind="applause", start=9.5, end=9.9), CapEvent(kind="laughter", start=9.6, end=9.9)]
    sig = signals(
        tr, np.linspace(0.1, 1.0, 10), heatmap=bins, hotspots=hotspots, events=events, chat_bursts=[(9, 10)]
    )
    assert tags(tr, sig)[9] == [
        "{energetic}", "[replayed]", "[comments: 2]", "[chat burst]", "[Laughter]", "[Applause]",
    ]  # fmt: skip


def test_chapter_assignment():
    tr = transcript((10.0, 12.0), (59.5, 61.0), (61.0, 62.0), (120.0, 121.0), (130.0, 131.0))
    chapters = [
        Chapter(start=0.0, end=60.0, title="Intro"),
        Chapter(start=60.0, end=120.0, title="The story"),
        Chapter(start=120.0, end=125.0, title="Outro"),
    ]
    tagged = apply_tags(tr, signals(tr), chapters)
    assert [s.chapter for s in tagged.sentences] == ["Intro", "Intro", "The story", "Outro", None]


def test_speech_rate():
    tr = transcript((0.0, 2.0), (3.0, 3.2))  # four words each, the second set in a fifth of a second
    sig = signals(tr)
    assert (sig.sentences["S1"].rate_wps, sig.sentences["S2"].rate_wps) == (2.0, 8.0)


# A range


def test_range_signals():
    tr = transcript((0.0, 1.0), (1.0, 4.0), (4.0, 5.0), (20.0, 22.0))
    rms = [0.1, 0.5, 0.5, 0.5, 0.9] + [0.2] * 18
    hotspots = [
        Hotspot(start=0.0, end=15.0, count=3, weight=6.0, hot=True),
        Hotspot(start=15.0, end=30.0, count=1, weight=2.0, hot=False),
    ]
    events = [
        CapEvent(kind="laughter", start=6.5, end=7.0),  # 1.5 s after S3 ends, 2.5 s after S2
        CapEvent(kind="applause", start=23.0, end=23.5),  # 1 s after S4 ends
    ]
    sig = signals(
        tr,
        rms,
        heatmap=heat([0.1 * (k + 1) for k in range(10)]),
        hotspots=hotspots,
        events=events,
        chat_bursts=[(21.0, 25.0)],
    )
    # Energy ranks: S1 0.1 lowest, S4 0.2, S2 0.5, S3 0.9 highest.
    first = range_signals(tr, sig, 0.0, 4.0)  # S1 and S2
    assert first.heat_max_pct == pytest.approx(3 / 9)
    assert first.comments_weight == 6.0 and not first.chat_burst
    assert first.energy_pct == pytest.approx((1 * 0.0 + 3 * 2 / 3) / 4)  # weighted by seconds in the range
    part = range_signals(tr, sig, 0.0, 2.5)  # S1 and half of S2
    assert part.energy_pct == pytest.approx((1 * 0.0 + 1.5 * 2 / 3) / 2.5)
    assert range_signals(tr, sig, 2.0, 2.0).energy_pct == pytest.approx(2 / 3)  # no length: a plain mean
    assert not first.laughter and not first.applause
    with_s3 = range_signals(tr, sig, 0.0, 5.0)
    assert with_s3.laughter and not with_s3.applause  # laughter in a sentence's halo counts for its range
    last = range_signals(tr, sig, 19.0, 23.0)  # S4, past the heatmap's end
    assert last.chat_burst and last.comments_weight == 2.0 and last.heat_max_pct == 0.0
    assert last.applause and not last.laughter
    assert last.energy_pct == pytest.approx(1 / 3)
    empty = range_signals(tr, sig, 100.0, 110.0)
    assert (empty.energy_pct, empty.comments_weight, empty.chat_burst, empty.laughter) == (
        0.0,
        0.0,
        False,
        False,
    )
    assert range_signals(tr, signals(tr, rms), 0.0, 4.0).heat_max_pct is None


def test_signals_when_nobody_speaks():
    tr = Transcript(
        engine="parakeet", engine_version="test", language=None, duration=10.0, words=[], sentences=[]
    )
    hotspots = [Hotspot(start=0.0, end=15.0, count=3, weight=6.0, hot=True)]
    sig = signals(tr, heatmap=heat([0.5] * 10), hotspots=hotspots)
    assert sig.sentences == {} and sig.has_heatmap and sig.has_comments
    assert apply_tags(tr, sig, []).sentences == []
    nothing = range_signals(tr, sig, 0.0, 10.0)
    assert (nothing.heat_max_pct, nothing.energy_pct, nothing.comments_weight) == (0.0, 0.0, 6.0)
