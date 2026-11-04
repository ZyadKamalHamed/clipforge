from itertools import combinations

import numpy as np
import pytest

from clipforge.asr.segment import mark_fillers
from clipforge.brain.heuristic import heuristic_candidates, heuristic_packaging, interest_curve, pick_peaks
from clipforge.brain.package import package
from clipforge.brain.select import discover
from clipforge.brain.snap import iou
from clipforge.models import (
    Candidate,
    Packaging,
    ProjectOptions,
    Sentence,
    SentenceSignals,
    Signals,
    Transcript,
    Word,
)
from tests.fakes import make_context, make_transcript, quiet_signals

TALK = make_transcript([f"Line number {k} of the talk." for k in range(1, 61)])  # 5 s each, 300 s
LONG = make_transcript([f"Line number {k} of the talk." for k in range(1, 181)])  # 900 s
GAPPY = make_transcript(  # 10 s each, with nothing said from 40 to 75 s
    [f"Line number {k} of the talk." for k in range(1, 9)],
    seconds_each=10.0,
    starts=[0.0, 10.0, 20.0, 30.0, 75.0, 85.0, 95.0, 105.0],
)
EVEN = "Basic mode: spaced evenly, as no signal stands out here"


def signals(
    tr: Transcript, *, energy=None, heat=None, chat=(), laughter=(), comments=None, **flags
) -> Signals:
    """Signals for every sentence of ``tr``: energy 0.5 unless given, replay ranks only with ``heat``, and
    chat bursts, laughter and comment weights (the busiest bucket weighs 4) on the sentences named."""
    energy, comments = energy or {}, comments or {}
    sentences = {
        s.id: SentenceSignals(
            energy_pct=energy.get(s.id, 0.5),
            heat_pct=heat.get(s.id, 0.0) if heat is not None else None,
            comments_n=1 if s.id in comments else 0,
            comments_w=comments.get(s.id, 0.0),
            chat_burst=s.id in chat,
            events=["laughter"] if s.id in laughter else [],
            rate_wps=2.0,
        )
        for s in tr.sentences
    }
    base = {
        "has_heatmap": heat is not None,
        "has_comments": bool(comments),
        "has_chat": bool(chat),
        "has_events": bool(laughter),
        "rms": [],
        "heat_p80": None,
        "max_bucket_weight": 4.0 if comments else 0.0,
        "sentences": sentences,
    }
    return Signals(**(base | flags))


def candidates(tr: Transcript = TALK, *bounds: float, **fields) -> list[Candidate]:
    """Basic mode's candidates for ``tr``, over the whole of it or from ``bounds`` (t0, then t1)."""
    return heuristic_candidates(make_context(tr, **fields), *bounds)


def spans(cands: list[Candidate]) -> list[tuple[str, str]]:
    """Each candidate's first and last sentence."""
    return [(c.start_sid, c.end_sid) for c in cands]


def timed(*bounds: tuple[float, float]) -> Transcript:
    """One-word sentences S1 on, each from and to the times given."""
    words = [Word(i=k, text=f"Line{k + 1}", start=a, end=b) for k, (a, b) in enumerate(bounds)]
    sentences = [
        Sentence(id=f"S{w.i + 1}", start=w.start, end=w.end, w0=w.i, w1=w.i, text=w.text) for w in words
    ]
    return make_transcript([]).model_copy(
        update={"duration": bounds[-1][1], "words": words, "sentences": sentences}
    )


def packaged(texts: list[str], **fields) -> Packaging:
    """Basic mode's packaging for one clip of every sentence of ``texts``, with the English fillers marked."""
    plain = make_transcript(texts, seconds_each=10.0)
    tr = plain.model_copy(update={"words": mark_fillers(plain.words, "en")})
    c = Candidate(
        id="k1",
        start_sid="S1",
        end_sid=f"S{len(texts)}",
        start_s=0.0,
        end_s=tr.duration,
        scores={},
        mode="basic",
    )
    return heuristic_packaging(make_context(tr, **fields), c)


# The interest curve


def test_interest_curve_shares_the_weights_over_the_signals_present():
    tr = make_transcript([f"Line {k}." for k in range(1, 7)], seconds_each=20.0)  # 120 s
    sig = signals(
        tr,
        heat={"S1": 1.0},
        chat={"S2"},
        energy={s.id: 1.0 if s.id == "S3" else 0.0 for s in tr.sentences},
        laughter={"S4"},
        comments={"S5": 2.0},
    )
    curve = interest_curve(tr, sig)
    assert len(curve) == 120
    middles = [curve[10 + 20 * k] for k in range(6)]  # smoothing over 9 s leaves the middle of 20 s alone
    assert middles == pytest.approx([0.35 / 1.3, 0.30 / 1.3, 0.25 / 1.3, 0.20 / 1.3, 0.10 / 1.3, 0.0])


def test_interest_curve_counts_comments_against_the_busiest_bucket():
    tr = make_transcript(["One.", "Two.", "Three."], seconds_each=20.0)
    curve = interest_curve(tr, signals(tr, comments={"S1": 6.0, "S2": 2.0}))
    assert [curve[10], curve[30], curve[50]] == pytest.approx([1.0, 0.5, 0.0])
    cold = signals(tr, comments={"S1": 6.0, "S2": 2.0}, has_comments=False)  # no bucket is hot
    assert not interest_curve(tr, cold).any()
    assert not interest_curve(tr, signals(tr, comments={"S1": 6.0}, max_bucket_weight=0.0)).any()


def test_interest_curve_leaves_out_signals_that_are_the_same_everywhere():
    tr = make_transcript(["One.", "Two.", "Three."], seconds_each=20.0)
    sig = signals(tr, heat={"S2": 1.0}, chat={"S1", "S2", "S3"}, has_events=True)  # flat energy, no laughter
    curve = interest_curve(tr, sig)
    assert [curve[10], curve[30], curve[50]] == pytest.approx([0.0, 1.0, 0.0])


def test_interest_curve_is_a_9_second_moving_average():
    tr = make_transcript(["One.", "Two.", "Three.", "Four."])  # 5 s each
    middle = interest_curve(tr, signals(tr, heat={"S2": 1.0}))
    assert np.round(middle * 9, 6).tolist() == [0, 1, 2, 3, 4, 5, 5, 5, 5, 5, 4, 3, 2, 1, 0, 0, 0, 0, 0, 0]
    edge = interest_curve(tr, signals(tr, heat={"S1": 1.0}))  # nothing is said before the start
    assert np.round(edge * 9, 6).tolist() == [5, 5, 5, 5, 5, 4, 3, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]


def test_interest_curve_takes_the_higher_value_where_sentences_share_a_second():
    tr = make_transcript(["One.", "Two."], seconds_each=5.5, starts=[0.0, 5.5])  # both cover second 5
    curve = interest_curve(tr, signals(tr, heat={"S1": 1.0}))
    assert np.round(curve * 9, 6).tolist() == [5, 6, 6, 6, 6, 5, 4, 3, 2, 1, 0]


def test_interest_curve_counts_a_sentence_missing_from_the_signals_as_quiet():
    tr = make_transcript(["One.", "Two.", "Three."], seconds_each=20.0)
    sig = signals(tr, heat={"S1": 0.5, "S2": 1.0, "S3": 0.5}, energy={s.id: 0.0 for s in tr.sentences})
    gap = sig.model_copy(update={"sentences": {sid: v for sid, v in sig.sentences.items() if sid != "S3"}})
    curve = interest_curve(tr, gap)
    assert [curve[10], curve[30], curve[50]] == pytest.approx([0.5, 1.0, 0.0])


def test_interest_curve_without_signals_is_zero_every_second():
    tr = make_transcript(["One.", "Two.", "Three."], starts=[0.0, 5.0, 10.5])  # ends at 15.5 s
    assert interest_curve(tr, quiet_signals()).tolist() == [0.0] * 16
    short = tr.model_copy(update={"duration": 12.0})  # the curve still reaches the last sentence
    assert len(interest_curve(short, quiet_signals())) == 16
    longer = tr.model_copy(update={"duration": 20.0})  # and on to the end of the media
    assert len(interest_curve(longer, quiet_signals())) == 20
    assert len(interest_curve(make_transcript([]), quiet_signals())) == 0


# Peaks


def test_pick_peaks_min_gap():
    curve = np.zeros(120)
    curve[[10, 25, 30, 50, 100]] = [0.8, 0.9, 1.0, 0.7, 0.6]
    assert pick_peaks(curve, 3) == [30, 10, 50]  # 25 is too close to 30; 20 s apart is far enough
    assert pick_peaks(curve, 2, min_gap_s=25.0) == [30, 100]
    assert pick_peaks(np.ones(50), 10) == [0, 20, 40]  # the earliest of equals first
    assert pick_peaks(curve, 0) == []
    assert pick_peaks(np.zeros(0), 3) == []


# Windows


def test_heuristic_windows_snap_to_sentences_and_length():
    found = candidates(TALK, signals=signals(TALK, laughter={"S20"}))  # laughter from 95 to 100 s
    top = found[0]
    assert (top.start_sid, top.end_sid, top.start_s, top.end_s) == ("S18", "S23", 85.0, 115.0)
    assert top.llm_score == pytest.approx(30.0)  # its mean interest is 30 % of the curve's highest point
    assert len(found) == 5
    for c in found:
        assert (c.start_s, c.end_s) == (TALK.sentence(c.start_sid).start, TALK.sentence(c.end_sid).end)
        assert 30.0 <= c.duration <= 60.0


@pytest.mark.parametrize(
    ("laughter", "length", "span"),
    [
        ("S2", "auto", ("S1", "S4")),  # from 8 s before the peak to the first end 20 s after it and 30 s on
        ("S2", "short", ("S1", "S3")),  # 30 to 35 s, as the options' range is narrower
        ("S2", "long", ("S1", "S5")),  # 60 s or more
        ("S3", "auto", ("S2", "S4")),  # 75 s is too long, so the last sentence goes and 30 s are left
        ("S4", "auto", ("S3", "S5")),  # 65 s, as dropping S5 would leave 20 s
        ("S5", "auto", ("S5", "S7")),  # 8 s before the peak is in the pause, so it starts after it
    ],
)
def test_heuristic_windows_fit_30_to_60_seconds(laughter, length, span):
    sig = signals(GAPPY, laughter={laughter})
    assert spans(candidates(GAPPY, signals=sig, options=ProjectOptions(length=length)))[0] == span


@pytest.mark.parametrize(
    ("bounds", "span"),
    [
        # S2 reaches 20 s past the peak at 30 s but lasts 29.5 s, so S3 joins
        ([(0, 21), (21, 50.5), (50.5, 60)], ("S2", "S3")),
        # S1 ends right at 8 s before the peak, so S2 is the one going on
        ([(0, 22), (22, 40), (40, 60)], ("S2", "S3")),
        # S1 is still going on 8 s before the peak, and 60 s is not too long
        ([(0, 22.5), (22.5, 40), (40, 60)], ("S1", "S3")),
        # S2 ends half a second short of 20 s after the peak
        ([(0, 25), (25, 49.5), (49.5, 60)], ("S1", "S3")),
        # 60.4 s is too long, so S3 goes
        ([(0, 28), (28, 36), (50.4, 60.4), (60.4, 70.4)], ("S1", "S2")),
        # exactly 60 s, though 64.01 minus 4.01 is 60.00000000000001 in floats
        ([(4.01, 34), (34, 40), (54.01, 64.01), (64.01, 74.01)], ("S1", "S3")),
        # exactly 30 s, though 32.05 minus 2.05 is 29.999999999999996 in floats
        ([(0, 2.05), (2.05, 32.05)], ("S2", "S2")),
    ],
)
def test_heuristic_window_rules_at_their_edges(bounds, span):
    assert spans(candidates(timed(*bounds))) == [span]  # one window, around the middle of what is said


def test_heuristic_window_reaches_back_at_the_end():
    found = candidates(TALK, signals=signals(TALK, laughter={"S60"}))  # the last 5 s
    assert spans(found)[0] == ("S55", "S60")  # 270 to 300 s: earlier sentences make up the 30 s


def test_heuristic_reads_to_the_last_sentence_when_the_duration_falls_short():
    tr = TALK.model_copy(update={"duration": 295.0})  # S60 starts at 295 s
    found = candidates(tr, 0.0, 295.0, signals=signals(tr, laughter={"S60"}))
    assert spans(found)[0] == ("S55", "S60")
    short = TALK.model_copy(update={"duration": 230.0})
    assert len(candidates(short, signals=signals(short, laughter={"S20"}))) == 5  # for the 300 s said


def test_heuristic_windows_end_with_the_last_sentence_when_the_media_runs_on():
    tr = TALK.model_copy(update={"duration": 330.0})  # nothing is said in the last 30 s
    found = candidates(tr, signals=signals(tr, laughter={"S20"}))
    assert len(found) == 6  # about one a minute of 330 s
    assert all(c.end_s <= 300.0 for c in found)


def test_heuristic_respects_range_bounds():
    sig = signals(TALK, laughter={"S30", "S48", "S49", "S50"})  # 145 to 150 s, and 235 to 250 s
    inside = candidates(TALK, 100.0, 200.0, signals=sig)
    assert spans(inside) == [("S28", "S33"), ("S21", "S26")]  # two for 100 s
    assert inside[0].llm_score == pytest.approx(100 / 6)  # against the highest point of the whole curve
    after = candidates(TALK, 200.0, signals=sig)  # to the end
    assert spans(after)[0] == ("S47", "S52")
    assert all(c.start_s >= 200.0 for c in after)
    assert spans(candidates(TALK, 280.0)) == [("S57", "S60")]  # 20 s left, and nothing before 280 s joins
    assert spans(candidates(TALK, 295.0)) == [("S60", "S60")]


def test_heuristic_without_signals_spaces_evenly():
    for sig in (quiet_signals(), signals(LONG)):  # no signal at all, or energy the same everywhere
        found = candidates(LONG, signals=sig)
        assert [c.start_s for c in found] == [20.0 + 60 * k for k in range(15)]  # one every 60 s of 900 s
        assert {c.duration for c in found} == {30.0}
        assert {c.llm_score for c in found} == {50.0}
        assert {c.reason for c in found} == {EVEN}
    assert spans(candidates(LONG, 100.0, 200.0)) == [("S24", "S29"), ("S34", "S39")]
    loud = signals(TALK, energy={f"S{k}": float(k <= 30) for k in range(1, 61)})  # loud for the first 150 s
    steady = candidates(TALK, 20.0, 100.0, signals=loud)  # the energy varies, but not here
    assert spans(steady) == [("S7", "S12"), ("S15", "S20")]
    assert {c.reason for c in steady} == {EVEN}


def test_heuristic_skips_windows_that_overlap_a_better_one():
    sig = signals(TALK, laughter={"S20", "S24"})  # peaks at 95 and 115 s
    found = candidates(TALK, signals=sig, options=ProjectOptions(length="long"))
    assert len(found) == 5
    assert spans(found)[0] == ("S18", "S29")  # 85 to 145 s
    assert ("S22", "S33") not in spans(found)  # 105 to 165 s overlaps it by half
    assert all(iou((a.start_s, a.end_s), (b.start_s, b.end_s)) <= 0.3 for a, b in combinations(found, 2))
    early = candidates(TALK, signals=signals(TALK, laughter={"S2"}))  # 5 to 10 s
    assert spans(early)[:2] == [("S1", "S6"), ("S8", "S13")]  # 15 to 45 s overlapped 0 to 30 s by a third
    tr = timed((0, 5), (5, 15), (15, 30), (30, 50), (50, 80))
    pair = candidates(tr, signals=signals(tr, laughter={"S2", "S3"}))  # peaks at 9 and 29 s
    assert spans(pair) == [("S1", "S3"), ("S3", "S4")]  # 0 to 30 s and 15 to 50 s: exactly 0.3 is allowed


def test_heuristic_orders_windows_by_their_mean_interest():
    loud = {"S10": 1.0, "S11": 1.0} | {f"S{k}": 0.9 for k in range(40, 45)}
    sig = signals(TALK, energy={f"S{k}": loud.get(f"S{k}", 0.0) for k in range(1, 61)})
    # 10 s at full energy from 45 s peak highest, but 25 s nearly as loud from 195 s make the better clip
    assert spans(candidates(TALK, signals=sig))[:2] == [("S39", "S44"), ("S9", "S14")]


def test_heuristic_puts_the_earlier_of_equally_interesting_windows_first():
    # replays of 0.7 at 45 s and energy of 0.98 at 195 s weigh the same (0.35 x 0.7 and 0.25 x 0.98), though
    # the floats make the first 0.24499999999999997
    energy = {f"S{k}": 0.98 if k == 40 else 0.0 for k in range(1, 61)}
    sig = signals(TALK, heat={"S10": 0.7}, energy=energy)
    assert spans(candidates(TALK, signals=sig))[:2] == [("S8", "S13"), ("S38", "S43")]


def test_heuristic_keeps_a_sentence_longer_than_the_range_whole():
    tr = make_transcript(["One long story.", "And another one."], seconds_each=70.0)
    assert spans(candidates(tr)) == [("S1", "S1"), ("S2", "S2")]


def test_heuristic_aims_for_the_target_count():
    sig = signals(TALK, laughter={"S20"})
    assert len(candidates(TALK, signals=sig)) == 5  # about one a minute of 300 s
    assert len(candidates(TALK, signals=sig, options=ProjectOptions(clip_count=2))) == 2
    assert len(candidates(TALK, 0.0, 60.0, signals=sig)) == 1  # one for a stretch under 75 s


def test_heuristic_candidates_are_basic_mode_placeholders():
    ctx = make_context(TALK, signals=signals(TALK, laughter={"S20"}))
    assert ctx.next_id() == "k1"  # another pass took k1
    found = heuristic_candidates(ctx)
    assert [c.id for c in found] == ["k2", "k3", "k4", "k5", "k6"]
    for c in found:
        assert (c.mode, c.passes_gates) == ("basic", True)
        assert c.reason == "Basic mode: picked from the video's laughter"
        assert c.scores == {"C1": 5, "C2": 5, "C3": 5, "C4": 5, "C5": 5, "C6": 5, "C7": 5, "C9": 5}


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        ({"heat": {"S20": 1.0}, "laughter": {"S20"}}, "replays and laughter"),
        (
            {"heat": {"S20": 1.0}, "chat": {"S3"}, "energy": {"S20": 0.9}, "comments": {"S9": 1.0}},
            "replays, chat bursts, energy and comments",
        ),
    ],
)
def test_heuristic_reason_names_the_signals_read(fields, named):
    found = candidates(TALK, signals=signals(TALK, **fields))
    assert found[0].reason == f"Basic mode: picked from the video's {named}"


def test_heuristic_puts_the_most_interesting_window_first():
    sig = signals(LONG, laughter={"S20", "S21", "S22", "S100"})  # 95 to 110 s, and 495 to 500 s
    assert spans(candidates(LONG, signals=sig))[:2] == [("S19", "S24"), ("S98", "S103")]
    far = candidates(LONG, signals=sig, focus_s=1100.0)  # nothing is within 3 minutes of it
    assert spans(far)[:2] == [("S19", "S24"), ("S98", "S103")]


@pytest.mark.parametrize("focus", [480.0, 305.0, 695.0])  # inside the window, and 3 minutes either side
def test_heuristic_puts_the_best_window_near_the_focus_first(focus):
    sig = signals(LONG, laughter={"S20", "S21", "S22", "S100"})
    assert spans(candidates(LONG, signals=sig, focus_s=focus))[:2] == [("S98", "S103"), ("S19", "S24")]


@pytest.mark.parametrize("focus", [210.5, 230.0])  # 3 minutes and half a second after that window, and on
def test_heuristic_never_drops_a_better_window_for_the_focus(focus):
    sig = signals(TALK, laughter={"S2"})  # 5 to 10 s, in the window from 0 to 30 s
    found = candidates(TALK, signals=sig, focus_s=focus)
    assert spans(found)[:2] == [("S8", "S13"), ("S1", "S6")]  # the best within reach, then the best


def test_heuristic_with_nothing_said():
    assert candidates(make_transcript([])) == []
    assert candidates(TALK, 300.0) == []  # no sentence starts that late


# Packaging


@pytest.mark.parametrize(
    ("texts", "title"),
    [
        (["This is the story of how I lost everything in one day."], "This is the story of how I lost"),
        (
            ["Procrastination absolutely destroys extraordinary ambitions."],
            "Procrastination absolutely destroys",
        ),
        (["Why do we wait until the last minute?"], "Why do we wait until the last minute?"),
        (["Nobody read it before it went out."], "Nobody read it before it went out"),
        (["Um, so NASA called us back."], "NASA called us back"),  # the filler is cut, then the "so"
        (["Now, the panic monster is dormant most of the time,"], "The panic monster is dormant"),
        (["Yeah, so like when you're in it, it's hard."], "When you're in it, it's hard"),
        (["So what?"], "So what?"),  # a word to start on is kept when only 2 would be left
        (["Deadlines change everything.", "Then the panic monster wakes up."], "Deadlines change everything"),
        (["Yeah.", "The deadline changes everything for us."], "The deadline changes everything for us"),
        (["iPhone sales doubled that year."], "iPhone sales doubled that year"),
        (["Okay.", "Right then."], "Okay"),  # no sentence has 3 words, so the first will do
        (["Um, uh."], "Um, uh"),  # nothing but fillers: the sentence as said
        (["..."], ""),  # nothing to title
    ],
)
def test_heuristic_packaging_title_rules(texts, title):
    p = packaged(texts)
    assert (p.hook_title, p.tiktok_caption, p.instagram_caption) == (title, title, title)


def test_heuristic_packaging_is_basic():
    p = packaged(["The deadline changes everything for us.", "Then the panic monster wakes up."])
    assert (p.hook_alts, p.accent_word, p.emoji_cues, p.fact_cards, p.cold_open_sid) == (
        [],
        None,
        [],
        [],
        None,
    )
    assert (p.mood, p.energy, p.mode, p.credit) == ("calm", 3, "basic", "Source: TED")
    assert packaged(["The deadline changes everything for us."], rights_basis="own").credit is None


def test_heuristic_keywords():
    p = packaged(
        [
            "Really, every deadline wakes the panic monster.",
            "The monster panics, and the DEADLINE wins.",
            "Really, nobody likes a deadline, really.",
        ]
    )
    assert p.keywords == ["deadline", "monster", "wakes"]  # the most said first, then the first said
    assert p.tiktok_hashtags == p.instagram_hashtags == ["#deadline", "#monster", "#wakes"]
    assert packaged(["In 2024, 2024 was the year of 2024."]).keywords == ["year"]


def test_heuristic_emphasis_is_the_numbers_said():
    p = packaged(["In (1990) he lost $4M, then 1990 again.", 'By "2001." it was 50%, maybe 7.'])
    assert p.emphasis == ["1990", "$4M", "2001"]  # each once, without the marks around it, at most 3
    assert packaged(["Nothing to count here."]).emphasis == []


# The fallback for discover and package


def test_discover_and_package_fall_back_to_basic_mode():
    ctx = make_context(TALK, signals=signals(TALK, laughter={"S20"}))
    found = discover(ctx)
    assert spans(found)[0] == ("S18", "S23")
    assert {c.mode for c in found} == {"basic"}
    packed = package(ctx, found[:2])
    assert [c.packaging for c in packed] == [heuristic_packaging(ctx, c) for c in found[:2]]
