import numpy as np
import pytest

from clipforge.config import Settings
from clipforge.models import (
    Analysis,
    ClipRow,
    FactCard,
    Packaging,
    Sentence,
    Shot,
    SpeakerTurn,
    Track,
    Transcript,
    Word,
)
from clipforge.render.compositor import overlays, paint
from clipforge.render.edl import CARD_GONE, EDL, build_edl
from clipforge.render.styles import Style, style_for
from clipforge.render.timeline import Timeline, output_fps

FPS = 30.0
FRAME = 1 / FPS


def face_track(tid, shot, t0, t1, x, size=0.1):
    """A still face with its eyes at (``x``, 0.35) from ``t0`` to ``t1``, sampled at 10 fps."""
    n = round((t1 - t0) * 10)
    return Track(
        id=tid,
        shot=shot,
        times=[round(t0 + i * 0.1, 6) for i in range(n)],
        boxes=[(x - size / 2, 0.3, size, size * 1.3)] * n,
        eyes=[(x, 0.35)] * n,
        scores=[0.9] * n,
        src=[None] * n,
    )


def analysis(shots=None, tracks=None, *, turns=(), layout="face"):
    return Analysis(
        version=1,
        key="k",
        start_s=0.0,
        end_s=60.0,
        width=1920,
        height=1080,
        fps=30.0,
        cuts=[],
        shots=shots or [Shot(start_s=0.0, end_s=60.0, kind="single", tracks=[0], quality=8.0)],
        tracks=tracks or [face_track(0, 0, 0.0, 60.0, 0.5)],
        turns=list(turns),
        motion=[],
        quality=8.0,
        layout=layout,
        layout_reason="One speaker: the speaker fills the frame",
    )


def steady(seconds, step=0.3):
    """Words every ``step`` seconds with 50 ms between them: nothing to trim."""
    return [(round(i * step, 6), round(i * step + step - 0.05, 6)) for i in range(round(seconds / step))]


def setup(
    spans,
    *,
    split=None,
    texts=None,
    fillers=(),
    style=None,
    src_duration=60.0,
    clip_end=None,
    an=None,
    packaging=None,
    credit_name=None,
    gameplay=None,
    edits=None,
):
    """Words at ``spans``; with ``split`` the words from that index on are a second sentence, the payoff."""
    words = [
        Word(i=i, text=texts[i] if texts else f"w{i}", start=a, end=b, filler=i in fillers)
        for i, (a, b) in enumerate(spans)
    ]
    split = split or len(words)
    sents = [Sentence(id="S1", start=words[0].start, end=words[split - 1].end, w0=0, w1=split - 1, text="a")]
    if split < len(words):
        last = len(words) - 1
        sents.append(
            Sentence(id="S2", start=words[split].start, end=words[-1].end, w0=split, w1=last, text="b")
        )
    tr = Transcript(
        engine="t", engine_version="1", language="en", duration=src_duration, words=words, sentences=sents
    )
    clip = ClipRow(
        id="c1",
        project_id="p1",
        start_s=words[0].start,
        end_s=clip_end or words[-1].end,
        sentence_ids=[s.id for s in sents],
        edits=edits,
    )
    return build_edl(
        clip,
        tr,
        an or analysis(),
        packaging or Packaging(hook_title="A hook"),
        style or Style(),
        src_fps=FPS,
        src_duration=src_duration,
        credit_name=credit_name,
        gameplay=gameplay,
    )


def gap_after(edl, i):
    """The output silence between kept word i and the next kept word."""
    return edl.words[i + 1].start - edl.words[i].end


def centre(rect):
    return rect[0] + rect[2] / 2


def test_output_fps_follows_the_source():
    assert output_fps(29.97, False) == 29.97 and output_fps(59.94, False) == 59.94
    assert output_fps(59.94, True) == 30.0 and output_fps(25.0, True) == 25.0
    assert output_fps(24.0, False) == 24.0 and output_fps(120.0, False) == 60.0
    assert output_fps(15.0, False) == 23.976


def test_timeline_maps_source_to_output_on_the_frame_grid():
    tl = Timeline.from_ranges([(10.0, 12.01), (15.0, 16.0)], 30.0)
    assert [round(s.out_start * 30, 6) for s in tl.segments] == [0, 60]
    assert tl.frames == 90 and tl.duration == pytest.approx(3.0)
    assert tl.to_output(11.0) == pytest.approx(1.0) and tl.to_output(13.0) is None
    assert tl.to_source(2.5) == pytest.approx(15.5)
    fast = Timeline.from_ranges([(0.0, 2.2)], 30.0, speed=1.1)
    assert fast.frames == 60 and fast.to_output(1.1) == pytest.approx(1.0)


def test_pause_over_350_ms_shrinks_to_120_ms():
    edl = setup([(0.0, 0.4), (1.0, 1.4), (1.5, 1.9)])
    assert gap_after(edl, 0) == pytest.approx(0.12, abs=FRAME)
    assert gap_after(edl, 1) == pytest.approx(0.1, abs=FRAME)
    assert len(edl.segments) == 2


def test_pause_before_the_payoff_keeps_up_to_500_ms():
    long = setup([(0.0, 0.4), (0.5, 0.9), (1.9, 2.3)], split=2)
    assert gap_after(long, 1) == pytest.approx(0.5, abs=FRAME)
    short = setup([(0.0, 0.4), (0.5, 0.9), (1.35, 1.7)], split=2)
    assert gap_after(short, 1) == pytest.approx(0.45, abs=FRAME) and len(short.segments) == 1


def test_the_payoff_gets_a_soft_hit():
    edl = setup([(0.0, 0.4), (0.5, 0.9), (1.9, 2.3)], split=2)
    assert edl.payoff_s == edl.words[2].start
    assert [(c["kind"], c["on"]) for c in edl.sfx] == [("hit", edl.payoff_s)]
    quiet = setup([(0.0, 0.4), (0.5, 0.9), (1.9, 2.3)], split=2, style=Style(sfx=False))
    assert quiet.sfx == [] and setup([(0.0, 0.4), (0.5, 0.9)]).sfx == []


def test_isolated_filler_is_cut_but_never_like():
    texts = ["so", "um,", "like", "this"]
    spans = [(0.0, 0.3), (0.35, 0.6), (0.65, 0.9), (0.95, 1.3)]
    edl = setup(spans, texts=texts, fillers=(1, 2))
    assert [w.text for w in edl.words] == ["so", "like", "this"]
    assert gap_after(edl, 0) == pytest.approx(0.1, abs=FRAME)
    kept = setup(spans, texts=texts, fillers=(1, 2), style=Style(filler_removal=False))
    assert [w.text for w in kept.words] == texts


def test_a_leading_filler_is_cut_from_the_start():
    spans, texts = [(0.0, 0.3), (0.8, 1.1), (1.2, 1.5)], ["um", "so", "this"]
    edl = setup(spans, texts=texts, fillers=(0,))
    assert [w.text for w in edl.words] == ["so", "this"]
    assert edl.words[0].start == pytest.approx(0.06, abs=FRAME)
    untrimmed = setup(spans, texts=texts, fillers=(0,), style=Style(pause_trim=False))
    assert untrimmed.words[0].start == pytest.approx(0.5, abs=FRAME)


def test_pause_trim_off_keeps_every_gap():
    edl = setup([(0.0, 0.4), (1.0, 1.4), (1.5, 1.9)], style=Style(pause_trim=False))
    assert gap_after(edl, 0) == pytest.approx(0.6, abs=FRAME) and len(edl.segments) == 1


def test_clip_ends_250_ms_after_the_last_word():
    edl = setup([(0.0, 0.4), (0.5, 0.9)], clip_end=5.0)
    assert edl.duration == pytest.approx(edl.words[-1].end + 0.25, abs=FRAME)
    assert edl.audio_fade_s == 0.15


def test_clip_end_stops_before_the_next_word():
    edl = setup([(0.0, 0.4), (0.5, 0.9), (1.0, 1.3)], split=2, clip_end=0.9)
    assert [w.text for w in edl.words] == ["w0", "w1"]
    assert edl.duration == pytest.approx(1.0, abs=FRAME)


def test_edl_clamps_the_range_to_the_source():
    edl = setup([(9.0, 9.4), (9.5, 9.9)], src_duration=10.0, clip_end=12.0)
    assert edl.segments[-1].src_end <= 10.0 + 1e-6 and edl.segments[0].src_start >= 0.0
    assert edl.duration == pytest.approx(1.0, abs=FRAME)


def test_auto_pace_speeds_up_slow_speech_only():
    slow = steady(6.0, step=0.5)  # 2 words a second
    assert {s.speed for s in setup(slow).segments} == {1.0}
    assert {s.speed for s in setup(slow, style=Style(auto_pace=True)).segments} == {1.1}
    assert {s.speed for s in setup(steady(6.0), style=Style(auto_pace=True)).segments} == {1.0}


def test_edl_round_trips_as_json():
    edl = setup([(0.0, 0.4), (1.0, 1.4), (1.5, 1.9)])
    assert EDL.model_validate_json(edl.model_dump_json()) == edl
    assert (edl.width, edl.height, edl.fps, edl.clip_id) == (1080, 1920, 30.0, "c1")
    (span,) = edl.spans
    assert span.layout == "face" and len(span.crops) == 1
    assert len(span.crops[0]) == round(edl.duration * edl.fps)
    assert edl.hook["title"] == "A hook" and edl.credit is None


def test_wide_two_shot_stacks_two_panels():
    shots = [
        Shot(start_s=0.0, end_s=2.0, kind="single", tracks=[0], quality=8.0),
        Shot(start_s=2.0, end_s=60.0, kind="two_shot", tracks=[1, 2], quality=8.0),
    ]
    tracks = [
        face_track(0, 0, 0.0, 2.0, 0.5),
        face_track(1, 1, 2.0, 7.0, 0.25),
        face_track(2, 1, 2.0, 7.0, 0.75),
    ]
    face, stacked = setup(steady(6.0), an=analysis(shots, tracks)).spans
    assert (face.layout, stacked.layout) == ("face", "stacked")
    assert face.end == pytest.approx(2.0, abs=FRAME) and stacked.start == face.end
    assert stacked.boxes == [(0, 0, 1080, 960), (0, 960, 1080, 960)]
    frames = round((stacked.end - stacked.start) * FPS)
    assert [len(c) for c in stacked.crops] == [frames, frames]
    top, bottom = stacked.crops[0][-1], stacked.crops[1][-1]
    assert centre(top) == pytest.approx(480, abs=2) and centre(bottom) == pytest.approx(1440, abs=2)
    assert top[2] * 960 == pytest.approx(top[3] * 1080, rel=0.01)


def test_a_shot_is_as_long_as_its_part_in_the_clip():
    # the analysis starts a second before the clip, so the first shot runs 2.27 s there but 0.27 s here
    shots = [
        Shot(start_s=0.0, end_s=2.27, kind="single", tracks=[0], quality=8.0),
        Shot(start_s=2.27, end_s=60.0, kind="screen", tracks=[], quality=8.0),
    ]
    words = [(a + 2.0, b + 2.0) for a, b in steady(6.0)]
    (span,) = setup(words, an=analysis(shots, [face_track(0, 0, 0.0, 2.27, 0.5)])).spans
    assert span.layout == "fit" and span.start == 0.0


def test_camera_cuts_to_whoever_is_talking():
    shots = [Shot(start_s=0.0, end_s=60.0, kind="group", tracks=[1, 2], quality=8.0)]
    tracks = [face_track(1, 0, 0.0, 7.0, 0.25), face_track(2, 0, 0.0, 7.0, 0.75)]
    turns = [SpeakerTurn(start_s=0.0, end_s=3.0, track=1), SpeakerTurn(start_s=3.0, end_s=7.0, track=2)]
    (span,) = setup(steady(6.0), an=analysis(shots, tracks, turns=turns)).spans
    xs = [centre(r) for r in span.crops[0]]
    switch = round(3.0 * FPS)
    assert set(xs[:switch]) == {480} and set(xs[switch:]) == {1440}


def test_the_crop_keeps_a_fast_face_inside_it():
    """Round 4's TED close-up: a face over half the crop's width moved 350 px in a third of a second and
    slid a quarter out of the crop. The camera is given the face's box, not just its eyes."""
    times = [round(i * 0.1, 6) for i in range(60)]
    left = [0.57 if t < 3 else max(0.39, 0.57 - 0.52 * (t - 3)) for t in times]
    track = Track(
        id=0,
        shot=0,
        times=times,
        boxes=[(x, 0.25, 0.18, 0.4) for x in left],
        eyes=[(x + 0.09, 0.4) for x in left],
        scores=[0.9] * len(times),
        src=[None] * len(times),
    )
    edl = setup(steady(6.0), an=analysis(tracks=[track]))
    tl = edl.timeline()
    (span,) = edl.spans
    for f, (cx, _, cw, _) in enumerate(span.crops[0]):
        src = tl.to_source(f / edl.fps)
        k = min(range(len(times)), key=lambda i: abs(times[i] - src))
        assert cx <= left[k] * 1920 + 0.5 and (left[k] + 0.18) * 1920 <= cx + cw + 0.5, f


def test_punch_ins_start_on_emphasis_sentences():
    texts = ["w"] * 10 + ["a", "million"] + ["w"] * 8
    pk = Packaging(hook_title="A hook", emphasis=["a million"])
    edl = setup(steady(6.0), split=10, texts=texts, packaging=pk)
    (zoom,) = edl.zooms
    assert zoom.start == pytest.approx(3.0, abs=FRAME) and zoom.scale == 1.08 and zoom.end - zoom.start >= 2.0
    crops = edl.spans[0].crops[0]
    assert crops[round(2.0 * FPS)][2] == 608 and crops[round(4.0 * FPS)][2] == round(608 / 1.08)
    assert [w.text for w in edl.words if w.emphasis] == ["million"]
    off = setup(steady(6.0), split=10, texts=texts, packaging=pk, style=Style(punch_ins=False))
    assert off.zooms == [] and {r[2] for r in off.spans[0].crops[0]} == {608}


def test_gameplay_layout_needs_a_gameplay_video():
    item = {"path": "/library/run.mp4", "start_s": 12.0}
    edl = setup(steady(3.0), an=analysis(layout="gameplay"), gameplay=item)
    (span,) = edl.spans
    assert span.layout == "gameplay" and span.boxes == [(0, 0, 1080, 1056)] and edl.gameplay == item
    assert span.crops[0][0][2:] == (1104, 1080)
    fallback = setup(steady(3.0), an=analysis(layout="gameplay"))
    assert fallback.spans[0].layout == "face" and fallback.gameplay is None and fallback.notes


def test_faces_are_mapped_onto_the_canvas():
    faces = setup(steady(3.0)).spans[0].faces
    assert faces and all(x >= 0 and x + w <= 1080 and y >= 0 and y + h <= 1920 for _, x, y, w, h in faces)
    _, x, _, w, _ = faces[0]
    assert x + w / 2 == pytest.approx(540, abs=3) and w == pytest.approx(192 * 1080 / 608, abs=3)


def test_credit_and_hook_follow_the_style():
    edl = setup(steady(6.0), credit_name="Lex Fridman")
    assert edl.credit["text"] == "Source: Lex Fridman" and (edl.hook["start"], edl.hook["end"]) == (0.0, 4.0)
    assert setup(steady(6.0), credit_name="Lex Fridman", style=Style(credit_line=False)).credit is None
    odd = Style(credit_format="Clip: {channel} {oops}", hook_whole_clip=True)
    other = setup(steady(6.0), credit_name="Lex Fridman", style=odd)
    assert other.credit["text"] == "Clip: Lex Fridman {oops}" and other.hook["end"] == other.duration


SIZES = [(1920, 1080), (1280, 720), (1440, 1080), (1080, 1920), (3840, 2160)]
SHOT_KINDS = {"stacked": "two_shot", "stream": "stream", "motion": "motion", "fit": "screen"}


@pytest.mark.parametrize("layout", ["face", "stacked", "gameplay", "stream", "motion", "fit"])
@pytest.mark.parametrize("size", SIZES)
def test_every_crop_stays_inside_the_source(size, layout):
    w, h = size
    shots = [Shot(start_s=0.0, end_s=60.0, kind=SHOT_KINDS.get(layout, "single"), tracks=[0, 1], quality=8.0)]
    tracks = [face_track(0, 0, 0.0, 7.0, 0.2), face_track(1, 0, 0.0, 7.0, 0.85)]
    motion = [(round(i * 0.1, 6), 0.1 + i * 0.012, 0.05) for i in range(70)]
    an = analysis(shots, tracks, layout=layout).model_copy(update={"width": w, "height": h, "motion": motion})
    edl = setup(steady(6.0), an=an, gameplay={"path": "run.mp4", "start_s": 0.0})
    assert [s.layout for s in edl.spans] == [layout]
    assert edl.spans[0].start == 0.0 and edl.spans[-1].end == pytest.approx(edl.duration)
    for span in edl.spans:
        frames = round((span.end - span.start) * edl.fps)
        assert len(span.crops) == len(span.boxes) and all(len(path) == frames for path in span.crops)
        for x, y, cw, ch in (rect for path in span.crops for rect in path):
            assert x >= 0 and y >= 0 and cw > 0 and ch > 0 and x + cw <= w and y + ch <= h
        for x, y, bw, bh in span.boxes:
            assert x >= 0 and y >= 0 and x + bw <= 1080 and y + bh <= 1920


def test_a_vertical_source_fills_the_canvas():
    an = analysis().model_copy(update={"width": 1080, "height": 1920})
    (span,) = setup(steady(3.0), an=an).spans
    assert set(span.crops[0]) == {(0, 0, 1080, 1920)}
    fit = setup(steady(3.0), an=an.model_copy(update={"layout": "fit"})).spans[0]
    assert fit.boxes == [(0, 0, 1080, 1920)]


def test_a_big_facecam_crop_still_fits():
    shots = [Shot(start_s=0.0, end_s=60.0, kind="stream", tracks=[0], quality=8.0)]
    an = analysis(shots, [face_track(0, 0, 0.0, 7.0, 0.5, size=0.3)], layout="stream")
    an = an.model_copy(update={"width": 1080, "height": 1920})
    cam, game = (path[0] for path in setup(steady(3.0), an=an).spans[0].crops)
    assert cam[0] >= 0 and cam[0] + cam[2] <= 1080 and cam[2] * 640 == pytest.approx(cam[3] * 1080, rel=0.01)
    assert game[2] * 1280 == pytest.approx(game[3] * 1080, rel=0.01)


def test_every_stream_span_crops_the_same_facecam():
    shots = [
        Shot(start_s=0.0, end_s=2.0, kind="stream", tracks=[0], quality=8.0),
        Shot(start_s=2.0, end_s=4.0, kind="single", tracks=[1], quality=8.0),
        Shot(start_s=4.0, end_s=60.0, kind="stream", tracks=[2], quality=8.0),
    ]
    tracks = [face_track(0, 0, 0.0, 2.0, 0.10, size=0.04), face_track(1, 1, 2.0, 4.0, 0.5, size=0.2)]
    tracks.append(face_track(2, 2, 4.0, 7.0, 0.11, size=0.04))
    edl = setup(steady(6.0), an=analysis(shots, tracks, layout="stream"))
    assert [s.layout for s in edl.spans] == ["stream", "face", "stream"]
    assert edl.spans[0].crops[0][0] == edl.spans[2].crops[0][0]


# The clip's own edits


def test_struck_words_are_cut_even_without_pause_trim():
    spans = steady(6.0)
    struck = [0, 5, 6, 19]
    edl = setup(spans, style=Style(pause_trim=False, filler_removal=False), edits={"struck": struck})
    assert [w.i for w in edl.words] == [i for i in range(len(spans)) if i not in struck]
    kept = [(s.src_start, s.src_end) for s in edl.segments]
    for i, (a, b) in enumerate(spans):
        heard = any(lo <= (a + b) / 2 <= hi for lo, hi in kept)
        assert heard == (i not in struck), i


def test_word_fixes_show_in_captions():
    edl = setup(steady(6.0), edits={"word_fixes": {"2": "Hello", "99": "Nowhere"}})
    assert [w.text for w in edl.words[:4]] == ["w0", "w1", "Hello", "w3"]
    shown = [pw.text.casefold() for page in edl.pages for pw in page.words]
    assert "hello" in shown and "w2" not in shown and "nowhere" not in shown


def test_card_outside_the_range_is_left_out_with_a_note():
    spans = steady(16.0)
    cards = [FactCard(sid="S1", text="Still here"), FactCard(sid="S2", text="Cut off")]
    pack = Packaging(hook_title="A hook", fact_cards=cards)
    edl = setup(spans, split=30, clip_end=spans[29][1], packaging=pack)  # the clip now ends with S1
    assert [c["text"] for c in edl.cards] == ["Still here"] and edl.notes.count(CARD_GONE) == 1
    gone = pack.model_copy(update={"fact_cards": [cards[1], FactCard(sid="S2", text="Also cut")]})
    edl = setup(spans, split=30, packaging=gone, edits={"struck": list(range(30, len(spans)))})
    assert edl.cards == [] and edl.notes.count(CARD_GONE) == 1  # its sentence was struck, the same
    assert CARD_GONE not in setup(spans, split=30, packaging=pack).notes
    assert CARD_GONE == "A fact card's sentence is no longer in the clip, so the card is left out"


def test_edl_has_no_hook_or_cards_when_off():
    spans = steady(16.0)
    pack = Packaging(hook_title="A hook", fact_cards=[FactCard(sid="S1", text="A fact")])
    on = setup(spans, packaging=pack, credit_name="Lex Fridman")
    assert on.hook is not None and on.cards and on.credit is not None
    off = setup(spans, packaging=pack, credit_name="Lex Fridman", style=Style(hook=False, cards=False))
    assert off.hook is None and off.cards == [] and off.notes == on.notes  # no note that it failed
    assert off.credit["text"] == "Source: Lex Fridman" and off.credit["box"][1] < on.credit["box"][1]
    assert setup(spans, packaging=pack, style=Style(cards=False)).hook is not None
    assert setup(spans, packaging=pack, style=Style(hook=False)).cards


def bars(edl, t):
    return [s for s, _ in overlays(edl, t) if (s.x, s.y, s.h) == (0, 1244, 6)]


def test_progress_bar_drawn_in_the_accent_colour():
    edl = setup(steady(6.0), style=Style(progress_bar=True, accent="#FF0000"))
    half = edl.duration / 2
    (bar,) = bars(edl, half)
    assert bar.w == round(1080 * half / edl.duration)
    canvas = np.zeros((1920, 1080, 3), np.uint8)
    paint(canvas, edl, half)
    assert canvas[1244, 10].tolist() == canvas[1249, 10].tolist() == [0, 0, 255]  # BGR
    assert canvas[1243, 10].tolist() == canvas[1250, 10].tolist() == [0, 0, 0]
    assert canvas[1247, 1070].tolist() == [0, 0, 0]  # not played yet
    assert bars(edl, 0.0) == [] and bars(edl, edl.duration - FRAME)[0].w >= 1080 - 10


def test_progress_bar_off_by_default():
    assert Settings().progress_bar is False and Style().progress_bar is False
    assert bars(setup(steady(6.0)), 1.0) == []
    assert style_for(Settings(progress_bar=True), {}).progress_bar is True
    assert style_for(Settings(progress_bar=True), {"progress_bar": False}).progress_bar is False
    assert style_for(Settings(), {"progress_bar": True}).progress_bar is True
