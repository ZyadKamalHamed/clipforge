import pytest

from clipforge.brain.snap import (
    LEAD_MAX,
    TAIL_MAX,
    THOUGHT_SLACK_S,
    dedupe,
    fit_duration,
    iou,
    length_flags,
    snap_all,
    snap_times,
    target_count,
)
from clipforge.models import Candidate, Transcript
from tests.fakes import make_transcript


def tens(n: int) -> Transcript:
    """``n`` sentences of 10 s each, back to back."""
    return make_transcript([f"Line number {k}." for k in range(1, n + 1)], seconds_each=10.0)


def joined(n: int, *cont: int) -> Transcript:
    """``tens(n)`` with each sentence S<k> for k in ``cont`` carrying on the one before it."""
    tr = tens(n)
    sentences = [s.model_copy(update={"cont": int(s.id[1:]) in cont}) for s in tr.sentences]
    return tr.model_copy(update={"sentences": sentences})


def timed(cid: str, start_s: float, end_s: float, score: float) -> Candidate:
    return Candidate(
        id=cid, start_sid="S1", end_sid="S1", start_s=start_s, end_s=end_s, scores={}, llm_score=score
    )


def spanning(cid: str, first: int, last: int, **fields) -> Candidate:
    """A candidate over sentences S<first> to S<last>, its times not yet snapped."""
    return Candidate(
        id=cid, start_sid=f"S{first}", end_sid=f"S{last}", start_s=0.0, end_s=0.0, scores={}, **fields
    )


# Snapping


def test_snap_lead_and_tail_limits():
    assert (LEAD_MAX, TAIL_MAX) == (0.35, 0.45)  # SPEC 9.2 step 2
    # "Three four." runs from 3.0 to 5.0: a 1.0 s gap before it and a 0.1 s gap after it
    tr = make_transcript(["One two.", "Three four.", "Five six."], seconds_each=2.0, starts=[0.0, 3.0, 5.1])
    assert snap_times(tr, 1, 1) == pytest.approx((3.0 - LEAD_MAX, 5.1 - 0.02))
    # and the other way round: 0.1 s before (2.1 to 4.1) and 1.0 s after
    tr = make_transcript(["One two.", "Three four.", "Five six."], seconds_each=2.0, starts=[0.0, 2.1, 5.1])
    assert snap_times(tr, 1, 1) == pytest.approx((2.0 + 0.02, 4.1 + TAIL_MAX))


def test_snap_clamps_to_media_bounds():
    tr = make_transcript(["One two.", "Three four."], seconds_each=2.0, starts=[0.1, 2.1])  # ends at 4.1
    assert snap_times(tr, 0, 1) == pytest.approx((0.0, 4.1))  # no lead before 0, no tail past the end
    longer = tr.model_copy(update={"duration": 4.3})
    assert snap_times(longer, 0, 1) == pytest.approx((0.0, 4.3))
    assert snap_times(tens(3), 1, 1) == pytest.approx((10.0, 20.0))  # words back to back leave no room


def test_snap_shrinks_lead_and_tail_to_stay_within_max_s():
    # "Three four." runs from 3.0 to 5.0 with a 1.0 s gap on each side
    tr = make_transcript(["One two.", "Three four.", "Five six."], seconds_each=2.0, starts=[0.0, 3.0, 6.0])
    start, end = snap_times(tr, 1, 1, max_s=2.4)
    assert end - start == pytest.approx(2.4)
    assert (3.0 - start) / (end - 5.0) == pytest.approx(LEAD_MAX / TAIL_MAX)  # both shrink alike
    assert snap_times(tr, 1, 1, max_s=60.0) == pytest.approx((3.0 - LEAD_MAX, 5.0 + TAIL_MAX))
    assert snap_times(tr, 1, 1, max_s=2.0) == pytest.approx((3.0, 5.0))  # words exactly max_s long keep none
    assert snap_times(tr, 1, 1, max_s=1.5) == pytest.approx((3.0 - LEAD_MAX, 5.0 + TAIL_MAX))  # over anyway


# Fitting the length


def test_fit_duration_trims_start_when_long():
    tr = tens(6)
    assert fit_duration(tr, 0, 5, 15.0, 35.0) == (3, 5, ["trimmed_start"])  # 60 s down to 30, payoff kept
    assert fit_duration(tr, 1, 3, 15.0, 35.0) == (1, 3, [])  # 30 s already fits
    one = make_transcript(["A very long sentence."], seconds_each=50.0)
    assert fit_duration(one, 0, 0, 15.0, 35.0) == (0, 0, [])  # a single sentence isn't cut


def test_fit_duration_extends_end_then_start_when_short():
    tr = tens(6)
    assert fit_duration(tr, 2, 2, 25.0, 30.0) == (2, 4, ["extended_end"])  # reaching exactly the max is fine
    assert fit_duration(tr, 5, 5, 25.0, 30.0) == (3, 5, ["extended_start"])  # nothing after the last
    assert fit_duration(tr, 4, 4, 25.0, 30.0) == (3, 5, ["extended_end", "extended_start"])
    assert fit_duration(tr, 2, 3, 20.0, 35.0) == (2, 3, [])  # exactly the min is long enough
    gap = make_transcript(["One.", "Two.", "Three."], seconds_each=10.0, starts=[0.0, 10.0, 50.0])
    assert fit_duration(gap, 1, 1, 15.0, 35.0) == (0, 1, ["extended_start"])  # the next would make 50 s
    two = make_transcript(["One.", "Two."], seconds_each=5.0)
    assert fit_duration(two, 0, 0, 15.0, 35.0) == (0, 1, ["extended_end", "short"])


def test_fit_duration_keeps_thoughts_whole():
    tr = joined(8, 3, 6)  # S3 carries on S2, and S6 carries on S5
    assert fit_duration(tr, 2, 3, 15.0, 75.0) == (1, 3, ["completed_start"])  # never starts on S3
    assert fit_duration(tr, 1, 4, 15.0, 75.0) == (1, 5, ["completed_end"])  # never ends just before S6
    assert fit_duration(tr, 0, 1, 15.0, 75.0) == (0, 2, ["completed_end"])


def test_fit_duration_may_run_over_the_range_to_keep_thoughts_whole():
    assert THOUGHT_SLACK_S == 10.0
    tr = joined(10, 8, 9)  # S7 to S9 is one thought
    assert fit_duration(tr, 1, 6, 15.0, 75.0) == (1, 8, ["completed_end"])  # 80 s, within the slack
    # 90 s whole is past the slack too, so the first thought goes
    assert fit_duration(tr, 0, 6, 15.0, 75.0) == (1, 8, ["trimmed_start", "completed_end"])
    # a clip already over the range gets no slack
    assert fit_duration(tr, 0, 8, 15.0, 75.0) == (2, 8, ["trimmed_start"])


def test_fit_duration_trims_whole_thoughts_from_a_long_clip():
    tr = joined(6, 4)  # S4 carries on S3
    assert fit_duration(tr, 0, 5, 15.0, 35.0) == (4, 5, ["trimmed_start"])  # not S4 to S6, which starts on S4


def test_fit_duration_cuts_a_thought_too_long_for_the_range_by_sentences():
    tr = joined(6, 2, 3, 4, 5, 6)  # all one 60 s thought
    assert fit_duration(tr, 0, 5, 15.0, 35.0) == (3, 5, ["trimmed_start"])
    assert fit_duration(tr, 2, 3, 15.0, 35.0) == (2, 3, [])  # the clip as it came, since it can't be whole


def test_fit_duration_gains_whole_thoughts_when_short():
    assert fit_duration(joined(6, 4), 1, 1, 15.0, 40.0) == (1, 3, ["extended_end"])  # S3 with S4, not alone
    assert fit_duration(joined(6, 5), 5, 5, 15.0, 40.0) == (3, 5, ["extended_start"])  # S4 with S5
    assert fit_duration(joined(4, 3, 4), 0, 0, 15.0, 25.0) == (0, 0, ["short"])  # S2 to S4 would make 40 s


def test_length_flags():
    assert length_flags(10.0, 21.999) == (["too_short"], [])
    assert length_flags(10.0, 22.0) == ([], [])  # exactly 12 s is long enough
    assert length_flags(0.0, 90.0004) == ([], [])  # counted to the millisecond, so not over 90 s
    assert length_flags(0.0, 90.001) == ([], ["over_90s_instagram"])
    assert length_flags(0.0, 180.0) == ([], ["over_90s_instagram"])
    assert length_flags(0.0, 180.001) == (["too_long"], ["over_90s_instagram"])
    assert length_flags(5.0, 5.0) == (["too_short"], [])


# Overlaps


def test_iou():
    assert iou((0.0, 10.0), (5.0, 15.0)) == pytest.approx(1 / 3)
    assert iou((0.0, 10.0), (2.0, 4.0)) == pytest.approx(0.2)
    assert iou((0.0, 10.0), (0.0, 10.0)) == 1.0
    assert iou((0.0, 10.0), (10.0, 20.0)) == 0.0
    assert iou((0.0, 10.0), (30.0, 40.0)) == 0.0
    assert iou((5.0, 5.0), (5.0, 5.0)) == 0.0  # nothing to overlap


def test_dedupe_keeps_higher_score():
    cands = [
        timed("k1", 0.0, 30.0, 50.0),
        timed("k2", 5.0, 35.0, 80.0),  # overlaps k1 by 25 of 35 s
        timed("k3", 40.0, 70.0, 60.0),
        timed("k4", 60.0, 90.0, 70.0),  # overlaps k3 by 10 of 50 s, which is allowed
        timed("k5", 100.0, 110.0, 40.0),
        timed("k6", 107.0, 110.0, 30.0),  # an IoU of exactly 0.3 is allowed too
        timed("k7", 120.0, 130.0, 20.0),
        timed("k8", 125.0, 135.0, 10.0),  # overlaps k7 by a third, which is too much
        timed("k9", 140.0, 150.0, 5.0),
        timed("k10", 146.95, 150.0, 4.0),  # an IoU of 0.305 is just too much
    ]
    assert [c.id for c in dedupe(cands)] == ["k2", "k4", "k3", "k5", "k6", "k7", "k9"]
    assert [c.id for c in dedupe(cands[:2], threshold=0.9)] == ["k2", "k1"]


# How many clips


def test_target_count_table():
    assert [target_count(d) for d in (50, 75, 180, 239, 240, 600, 7200)] == [1, 2, 3, 4, 4, 10, 40]
    assert target_count(600, override=7) == 7
    assert target_count(600, override=99) == 40
    assert target_count(600, override=0) == 1


# All together


def test_snap_all_duration_flags():
    tr = tens(30)  # 5 minutes, sentences back to back so snapping adds no lead or tail
    cands = [
        spanning("k1", 2, 2),  # 10 s
        spanning("k2", 2, 4, cold_open_sid="S3"),  # 30 s
        spanning("k3", 2, 11),  # 100 s
        spanning("k4", 2, 20, red_flags=["missing_context"]),  # 190 s
        spanning("k5", 1, 30, notes=["focus"], cold_open_sid="S3"),  # 300 s, so trimmed to 200
    ]
    out = snap_all(cands, tr, 5.0, 200.0)
    assert [(c.id, c.start_sid, c.end_sid) for c in out] == [
        ("k1", "S2", "S2"),
        ("k2", "S2", "S4"),
        ("k3", "S2", "S11"),
        ("k4", "S2", "S20"),
        ("k5", "S11", "S30"),
    ]
    times = [t for c in out for t in (c.start_s, c.end_s)]
    assert times == pytest.approx([10.0, 20.0, 10.0, 40.0, 10.0, 110.0, 10.0, 200.0, 100.0, 300.0])
    assert [c.red_flags for c in out] == [
        ["too_short"],
        [],
        [],
        ["missing_context", "too_long"],
        ["too_long"],
    ]
    assert [c.notes for c in out] == [
        [],
        [],
        ["over_90s_instagram"],
        ["over_90s_instagram"],
        ["focus", "trimmed_start", "over_90s_instagram"],
    ]
    assert [c.cold_open_sid for c in out] == [None, "S3", None, None, None]  # S3 was trimmed out of k5
    assert cands[0].red_flags == [] and cands[4].start_sid == "S1"  # the candidates given are left alone
    assert snap_all(out, tr, 5.0, 200.0) == out  # snapping again adds nothing


def test_snap_all_extends_a_short_clip():
    (clip,) = snap_all([spanning("k1", 2, 2)], tens(30), 25.0, 35.0)
    assert (clip.start_sid, clip.end_sid, clip.notes, clip.red_flags) == ("S2", "S4", ["extended_end"], [])
    assert (clip.start_s, clip.end_s) == pytest.approx((10.0, 40.0))


def test_snap_all_flags_only_past_12_and_180_s():
    tr = make_transcript([f"Line number {k}." for k in range(1, 21)], seconds_each=12.0)  # back to back
    out = snap_all([spanning("k1", 2, 2), spanning("k2", 2, 16)], tr, 5.0, 200.0)
    assert [c.end_s - c.start_s for c in out] == pytest.approx([12.0, 180.0])
    assert [c.red_flags for c in out] == [[], []]  # exactly 12 s and exactly 180 s are allowed


def test_snap_all_keeps_a_cold_open_only_inside_the_clip():
    sids = ["S5", "S8", "S4", "S9", "S99"]  # the first and last sentences, one either side, and one not there
    cands = [spanning(f"k{n}", 5, 8, cold_open_sid=sid) for n, sid in enumerate(sids, 1)]
    assert [c.cold_open_sid for c in snap_all(cands, tens(30), 5.0, 200.0)] == ["S5", "S8", None, None, None]


def test_snap_all_keeps_a_long_clip_within_90_s():
    # 9.3 s sentences 0.7 s apart: S2 to S10 is 89.3 s of speech, past 90 s with a full lead and tail. The
    # clip shrunk to fit comes to 90 s plus float noise, which still counts as 90
    tr = make_transcript(
        [f"Line number {k}." for k in range(1, 12)], seconds_each=9.3, starts=[10.0 * k for k in range(11)]
    )
    (clip,) = snap_all([spanning("k1", 2, 10)], tr, 60.0, 90.0)
    assert clip.end_s - clip.start_s == pytest.approx(90.0)
    assert clip.notes == [] and clip.red_flags == []
