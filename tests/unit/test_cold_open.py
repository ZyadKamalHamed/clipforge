"""The cold open (SPEC 8.3): the clip opens on 2 to 4 seconds of the line the packaging picked, then a whoosh
into the clip's start. The teaser is the timeline's first segment, so word timing, the caption check and the
camera all treat the body as it was, moved later by the teaser's length."""

import pytest

from clipforge.models import ClipRow, Packaging, Sentence, Track, Transcript, Word
from clipforge.render.edl import (
    COLD_AT_START,
    COLD_MAX_S,
    COLD_MIN_S,
    COLD_SHORT,
    COLD_STRUCK,
    EPS,
    NO_COLD_LINE,
    build_edl,
    cold_open_range,
    sentence_ids,
)
from clipforge.render.qa import _captions
from clipforge.render.styles import Style
from clipforge.render.timeline import Timeline
from tests.unit.test_edl import FPS, FRAME, analysis, face_track

# Words every 0.3 s, each 0.25 s long, in five sentences: S2 is 1.15 s long, S3 2.65 s and S5 nearly 9 s.
SENTENCES = {"S1": (0, 4), "S2": (5, 8), "S3": (9, 17), "S4": (18, 29), "S5": (30, 59)}
LAST = {w1 for _, w1 in SENTENCES.values()}


def transcript() -> Transcript:
    words = [
        Word(
            i=i,
            text=f"w{i}." if i in LAST else f"w{i}",
            start=round(i * 0.3, 6),
            end=round(i * 0.3 + 0.25, 6),
        )
        for i in range(60)
    ]
    sentences = [
        Sentence(id=sid, start=words[w0].start, end=words[w1].end, w0=w0, w1=w1, text=sid)
        for sid, (w0, w1) in SENTENCES.items()
    ]
    return Transcript(
        engine="t", engine_version="1", language="en", duration=60.0, words=words, sentences=sentences
    )


def build(sid="S3", *, cold=True, sfx=True, struck=(), an=None):
    tr = transcript()
    clip = ClipRow(
        id="c1",
        project_id="p1",
        start_s=0.0,
        end_s=tr.words[-1].end,
        sentence_ids=list(SENTENCES),
        edits={"struck": list(struck)} if struck else None,
    )
    return build_edl(
        clip,
        tr,
        an or analysis(),
        Packaging(hook_title="A hook", cold_open_sid=sid),
        Style(cold_open=cold, sfx=sfx),
        src_fps=FPS,
        src_duration=60.0,
        credit_name=None,
    )


def line(sid, struck=()):
    tr = transcript()
    kept = [w for w in tr.words if w.i not in set(struck)]
    return cold_open_range(tr, kept, sid, sentence_ids(tr, tr.words))


def test_teaser_segment_is_skipped_by_word_timing():
    tl = Timeline.from_ranges([(5.0, 20.0)], 30.0, teaser=(10.0, 12.0))
    first, body = tl.segments
    assert first.teaser and first.out_start == 0
    assert not body.teaser and body.out_start == pytest.approx(2.0)
    assert tl.to_output(11.0) == pytest.approx(2.0 + 6.0)
    assert tl.to_source(1.0) == pytest.approx(11.0)
    assert tl.duration == pytest.approx(17.0) and tl.teaser_end == pytest.approx(2.0)
    # a time the body cuts lands on the body's nearest edge, never on the teaser's
    gapped = Timeline.from_ranges([(5.0, 8.0), (15.0, 20.0)], 30.0, teaser=(10.0, 12.0))
    assert gapped.to_output(11.0) is None
    assert gapped.nearest_output(11.9) == pytest.approx(2.0 + 3.0)
    assert Timeline.from_ranges([(5.0, 20.0)], 30.0).teaser_end == 0.0


def test_cold_open_comes_first():
    plain, edl = build(cold=False), build()
    assert plain.cold_open is None and not any(s.teaser for s in plain.segments)
    cold = edl.cold_open
    out_end = cold["out_end"]
    assert cold["sid"] == "S3"
    assert cold["src_start"] == pytest.approx(2.7)
    assert cold["src_end"] == pytest.approx(5.4, abs=FRAME)
    assert out_end == pytest.approx(2.7, abs=FRAME)
    first = edl.segments[0]
    assert first.teaser and first.out_start == 0 and first.src_start == pytest.approx(2.7)
    assert not any(s.teaser for s in edl.segments[1:])
    assert edl.duration == pytest.approx(plain.duration + out_end)
    # the teaser's words come first, timed from 0, on caption pages of their own that end by out_end
    teaser = [w for w in edl.words if w.teaser]
    assert [w.i for w in teaser] == list(range(9, 18))
    assert edl.words[: len(teaser)] == teaser
    assert all(0 <= w.start <= w.end <= out_end + EPS for w in teaser)
    early = [p for p in edl.pages if p.start < out_end - EPS]
    assert [w.i for p in early for w in p.words] == list(range(9, 18))
    assert all(p.end <= out_end + EPS for p in early)
    # the body keeps the times it had without a cold open, moved later by out_end
    body = [w for w in edl.words if not w.teaser]
    assert [w.i for w in body] == [w.i for w in plain.words]
    for w, p in zip(body, plain.words, strict=True):
        assert (w.start, w.end) == (pytest.approx(p.start + out_end), pytest.approx(p.end + out_end))
    assert edl.payoff_s == pytest.approx(plain.payoff_s + out_end)
    later = [p for p in edl.pages if p.start >= out_end - EPS]
    assert [w.i for p in later for w in p.words] == [w.i for p in plain.pages for w in p.words]
    # the whoosh into the start is the cold open's own, and the hook shows from the first frame as before
    whoosh = [c for c in edl.sfx if c["kind"] == "whoosh"]
    assert [c["on"] for c in whoosh] == [pytest.approx(out_end)]
    assert [c["kind"] for c in edl.sfx] == ["whoosh", "hit"]
    assert edl.hook is not None
    assert _captions(edl) is None
    # your sound effects switch wins: off, there is no whoosh either
    quiet = build(sfx=False)
    assert quiet.sfx == [] and quiet.cold_open is not None


def test_cold_open_awkward_lines():
    assert NO_COLD_LINE == "No line was picked for a cold open, so this version has none"
    assert COLD_AT_START == "The cold open line is where the clip starts, so this version has none"
    assert COLD_SHORT == "The cold open line is under 2 seconds, so this version has none"
    assert COLD_STRUCK == "The cold open line's words are cut, so this version has none"
    assert (COLD_MIN_S, COLD_MAX_S) == (2.0, 4.0)
    assert line("S1") == (None, COLD_AT_START)
    assert line("S2") == (None, COLD_SHORT)
    assert line("S3", struck=range(9, 18)) == (None, COLD_STRUCK)
    assert line("S99") == (None, NO_COLD_LINE)
    assert line(None) == (None, NO_COLD_LINE)
    # the whole line, with 0.1 s after its last word but never into the next word
    (a, b), note = line("S3")
    assert note is None and (a, b) == (pytest.approx(2.7), pytest.approx(5.4))
    # a struck word ends the teaser before it, so it is never heard
    (a, b), _ = line("S3", struck=[16])
    assert (a, b) == (pytest.approx(2.7), pytest.approx(4.8))
    # a long line is cut to the last word that ends within 4 s
    (a, b), note = line("S5")
    assert note is None and a == pytest.approx(9.0)
    assert b - a <= COLD_MAX_S + EPS and b in {w.end for w in transcript().words}
    assert b == pytest.approx(12.85)
    # through the build, each note lands in the clip's notes and the clip plays as it would without one
    for sid, struck, note in [("S1", (), COLD_AT_START), ("S2", (), COLD_SHORT), ("S9", (), NO_COLD_LINE)]:
        edl = build(sid, struck=struck)
        assert note in edl.notes and edl.cold_open is None
        assert not any(s.teaser for s in edl.segments) and not any(w.teaser for w in edl.words)
    struck = build("S3", struck=range(9, 18))
    assert COLD_STRUCK in struck.notes and struck.cold_open is None
    assert all(note not in build(cold=False, sid=None).notes for note in (NO_COLD_LINE, COLD_SHORT))


def test_the_camera_cuts_hard_where_the_body_starts():
    """One shot whose face sits left for the clip's first 2 s and right after it: the teaser (from 2.7 s)
    shows it on the right, and the body's first frame is already framed on the left, with no pan between."""
    left, right = face_track(0, 0, 0.0, 2.0, 0.25), face_track(0, 0, 2.0, 60.0, 0.75)
    moving = Track(
        id=0,
        shot=0,
        times=left.times + right.times,
        boxes=left.boxes + right.boxes,
        eyes=left.eyes + right.eyes,
        scores=left.scores + right.scores,
        src=left.src + right.src,
    )
    edl = build(an=analysis(tracks=[moving]))
    jump = round(edl.cold_open["out_end"] * FPS)
    (span,) = edl.spans
    crops = span.crops[0]

    def middle(f):
        x, _, w, _ = crops[f]
        return x + w / 2

    assert middle(jump - 1) == pytest.approx(0.75 * 1920, abs=2)
    assert middle(jump) == pytest.approx(0.25 * 1920, abs=2)
