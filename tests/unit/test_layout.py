import numpy as np

from clipforge.models import Shot, Track
from clipforge.vision.layout import NO_GAMEPLAY, LayoutChoice, choose_layout, shot_layouts
from clipforge.vision.shots import FrameStat, classify_shots, clip_quality, frame_stats

TIMES = [round(i * 0.1, 6) for i in range(100)]


def tr(tid, x, *, w=0.15, y=0.3, times=TIMES, shot=0, score=0.9):
    n = len(times)
    return Track(
        id=tid,
        shot=shot,
        times=times,
        boxes=[(x, y, w, w * 1.3)] * n,
        eyes=[(x + w / 2, y + 0.4 * w)] * n,
        scores=[score] * n,
        src=[None] * n,
    )


def cam(tid, cx, cy, *, w=0.036, times=TIMES, shot=0):
    """A still face centred at (``cx``, ``cy``)."""
    return tr(tid, cx - w / 2, w=w, y=cy - 0.65 * w, times=times, shot=shot)


def stats(diff=0.005, sharp=300.0):
    return [FrameStat(diff=diff, sharp=sharp)] * len(TIMES)


def kinds(tracks, st=None):
    return [s.kind for s in classify_shots([], tracks, st or stats(), TIMES, 0.0, 10.0)]


def shot(kind, a=0.0, b=30.0, tracks=(0,)):
    return Shot(start_s=a, end_s=b, kind=kind, tracks=list(tracks), quality=8.0)


def pick(shots, *, wps=3.2, subdued=0.2, mode="auto", gameplay=True, forced=None):
    return choose_layout(
        shots,
        [],
        words_per_s=wps,
        subdued_share=subdued,
        gameplay_mode=mode,
        has_gameplay=gameplay,
        forced=forced,
    )


def test_frame_stats_measure_change_and_sharpness():
    flat = np.full((360, 640, 3), 128, np.uint8)
    stripes = np.dstack([((np.indices((360, 640)).sum(axis=0) // 20) % 2 * 255).astype(np.uint8)] * 3)
    a, b, c = frame_stats([flat, stripes, stripes])
    assert a.diff == 0.0 and b.diff > 0.2 and c.diff == 0.0
    assert b.sharp > a.sharp


def test_classify_single_two_shot_group():
    assert kinds([tr(0, 0.4)]) == ["single"]
    assert kinds([tr(0, 0.1), tr(1, 0.6)]) == ["two_shot"]
    assert kinds([tr(0, 0.05), tr(1, 0.4), tr(2, 0.75)]) == ["group"]
    assert kinds([tr(0, 0.3), tr(1, 0.38)]) == ["single"]  # too close together to stack
    assert kinds([tr(0, 0.1), tr(1, 0.6, w=0.03)]) == ["single"]  # one face too small
    assert kinds([tr(0, 0.1), tr(1, 0.6, times=TIMES[:30])]) == ["single"]  # together under half the shot
    split = [tr(0, 0.4, times=TIMES[:50]), tr(1, 0.1, times=TIMES[50:], shot=1)]
    split.append(tr(2, 0.6, times=TIMES[50:], shot=1))
    got = classify_shots([5.0], split, stats(), TIMES, 0.0, 10.0)
    assert [(s.kind, s.start_s, s.end_s, s.tracks) for s in got] == [
        ("single", 0.0, 5.0, [0]),
        ("two_shot", 5.0, 10.0, [1, 2]),
    ]


def test_classify_a_still_shot_without_a_face_as_screen():
    assert kinds([], stats(diff=0.003)) == ["screen"]  # a slide, a screen or a dark stage
    assert kinds([], stats(diff=0.06)) == ["motion"]


def test_classify_a_facecam_at_a_side_or_corner_over_any_content():
    side = [cam(0, 0.10, 0.49)]  # half way down the left edge, as on a Fortnite stream
    assert kinds(side, stats(diff=0.05)) == ["stream"]
    assert kinds(side) == ["stream"]  # over a still menu
    assert kinds([cam(0, 0.15, 0.19, w=0.055)], stats(diff=0.001)) == ["stream"]  # top left, over a quiz
    assert kinds([cam(0, 0.30, 0.49)], stats(diff=0.05)) == ["motion"]  # too far in for a facecam
    assert kinds([cam(0, 0.10, 0.49, w=0.15)]) == ["single"]  # too big for one


def test_classify_the_facecam_seen_longest():
    a, b = TIMES[:50], TIMES[50:]
    tracks = [cam(0, 0.10, 0.49, times=a), cam(1, 0.10, 0.49, times=b, shot=1)]
    tracks.append(cam(2, 0.86, 0.19, w=0.078, times=b, shot=1))  # a still picture in one shot's corner
    got = classify_shots([5.0], tracks, stats(diff=0.05), TIMES, 0.0, 10.0)
    assert [(s.kind, s.tracks) for s in got] == [("stream", [0]), ("stream", [1])]


def test_classify_a_face_held_in_a_corner_briefly_as_part_of_the_scene():
    head = [cam(0, 0.08, 0.78, w=0.026, times=TIMES[:15])]  # someone in the audience, for 1.5 s of 10
    got = classify_shots([1.5], head, stats(diff=0.004), TIMES, 0.0, 10.0)
    assert [s.kind for s in got] == ["screen", "screen"]


def test_classify_people_in_a_wide_shot_as_people():
    assert kinds([cam(0, 0.13, 0.45, w=0.05), cam(1, 0.6, 0.45, w=0.05)]) == ["two_shot"]
    trio = [cam(i, x, 0.45, w=0.035) for i, x in enumerate((0.12, 0.5, 0.88))]
    assert kinds(trio) == ["screen"]  # too small to crop, so shown whole
    crowd = [cam(i, 0.05 + 0.1 * i, 0.8, w=0.02) for i in range(9)]
    assert kinds(crowd) == ["screen"]


def test_classify_a_big_face_as_a_full_camera_shot():
    a, b = TIMES[:50], TIMES[50:]
    tracks = [cam(0, 0.15, 0.19, w=0.055, times=a), cam(1, 0.57, 0.65, w=0.22, times=b, shot=1)]
    got = classify_shots([5.0], tracks, stats(diff=0.002), TIMES, 0.0, 10.0)
    assert [(s.kind, s.tracks) for s in got] == [("stream", [0]), ("single", [1])]


def test_layout_face_for_an_energetic_single_speaker():
    got = pick([shot("single")])
    assert (got.kind, got.per_shot) == ("face", ["face"])
    assert got.reason


def test_layout_gameplay_for_a_slow_talky_single_speaker():
    got = pick([shot("single")], wps=2.0, subdued=0.7)
    assert (got.kind, got.per_shot) == ("gameplay", ["gameplay"])
    assert pick([shot("single", b=12.0)], wps=2.0, subdued=0.7).kind == "face"  # under 20 s of one voice


def test_layout_gameplay_needs_a_library_file():
    got = pick([shot("single")], wps=2.0, subdued=0.7, gameplay=False)
    assert (got.kind, got.reason) == ("face", NO_GAMEPLAY)
    assert NO_GAMEPLAY == "No gameplay video in the library yet, so the speaker fills the frame"


def test_layout_gameplay_off_and_always():
    assert pick([shot("single")], wps=2.0, subdued=0.7, mode="off").kind == "face"
    assert pick([shot("single")], mode="always").kind == "gameplay"
    assert pick([shot("two_shot", tracks=(0, 1))], mode="always").kind == "stacked"


def test_layout_stacked_for_a_wide_two_shot():
    got = pick([shot("two_shot", tracks=(0, 1))])
    assert got == LayoutChoice(
        "stacked", "Two people in a wide shot, both facing camera: stacked", ["stacked"]
    )
    short = pick([shot("single", 0, 10), shot("two_shot", 10, 12, (1, 2)), shot("single", 12, 30)])
    assert (short.kind, short.per_shot) == ("face", ["face", "face", "face"])
    mixed = pick([shot("single", 0, 10), shot("two_shot", 10, 30, (1, 2))])
    assert (mixed.kind, mixed.per_shot) == ("stacked", ["face", "stacked"])


def test_layout_without_faces_is_fit_or_motion():
    assert pick([shot("screen", tracks=())]).kind == "fit"
    assert (
        pick([shot("screen", tracks=())]).reason
        == "No faces and a still picture, like slides or a screen: shown whole"
    )
    assert pick([shot("motion", tracks=())]).kind == "motion"
    assert pick([shot("stream")]).kind == "stream"


def test_layout_forced_wins_with_its_reason():
    got = pick([shot("single")], forced="stacked")
    assert (got.kind, got.per_shot) == ("stacked", ["stacked"])
    assert "You chose" in got.reason
    assert pick([shot("single")], forced="gameplay", gameplay=False).reason == NO_GAMEPLAY


def test_shot_layouts_stack_only_long_two_shots():
    shots = [
        shot("single", 0, 5),
        shot("two_shot", 5, 6, (1, 2)),
        shot("two_shot", 6, 9, (3, 4)),
        shot("group", 9, 20, (5, 6, 7)),
    ]
    assert shot_layouts(shots, "face") == ["face", "face", "stacked", "face"]
    assert shot_layouts(shots, "stacked") == ["face", "face", "stacked", "face"]
    assert shot_layouts(shots, "gameplay") == ["gameplay"] * 4


def test_shot_layouts_suit_each_shot():
    shots = [
        shot("single", 0, 5),
        shot("screen", 5, 9, ()),
        shot("motion", 9, 14, ()),
        shot("stream", 14, 20),
        shot("two_shot", 20, 26, (1, 2)),
        shot("group", 26, 30, (3, 4, 5)),
    ]
    assert shot_layouts(shots, "face") == ["face", "fit", "motion", "stream", "stacked", "face"]
    assert shot_layouts(shots, "fit") == shot_layouts(shots, "face")


def test_shot_layouts_keep_the_split_in_a_mostly_facecam_clip():
    shots = [shot("stream", 0, 10), shot("screen", 10, 12, ()), shot("motion", 12, 20, ())]
    shots += [shot("stream", 20, 30), shot("single", 30, 34, (9,))]
    assert shot_layouts(shots, "stream") == ["stream", "stream", "stream", "stream", "face"]


def test_shot_layouts_hold_through_a_shot_under_a_second():
    shots = [shot("single", 0, 8), shot("screen", 8, 8.6, ()), shot("motion", 8.6, 20, ())]
    assert shot_layouts(shots, "motion") == ["face", "face", "motion"]
    assert shot_layouts([shot("screen", 0, 0.5, ()), shot("single", 0.5, 20)], "face") == ["face", "face"]


def test_shot_layouts_go_by_each_shots_length_in_the_clip():
    shots = [shot("two_shot", 0, 30, (0, 1)), shot("screen", 30, 60, ()), shot("stream", 60, 61)]
    assert shot_layouts(shots, "face") == ["stacked", "fit", "stream"]
    assert shot_layouts(shots, "face", [2.0, 1.5, 4.0]) == ["face", "stream", "stream"]


def test_layout_names_the_other_shots_layouts():
    got = pick([shot("single", 0, 20), shot("screen", 20, 30, ())])
    assert got == LayoutChoice(
        "face", "One speaker: the speaker fills the frame; other shots: whole frame", ["face", "fit"]
    )
    cam = pick([shot("stream", 0, 11), shot("single", 11, 37)])
    assert (cam.kind, cam.per_shot) == ("face", ["stream", "face"])
    assert cam.reason.endswith("; other shots: facecam over game")


def test_layout_is_the_one_covering_most_of_the_clip():
    got = pick([shot("single", 0, 10), shot("motion", 10, 30, ())])
    assert (got.kind, got.per_shot) == ("motion", ["face", "motion"])
    assert got.reason == "No faces and plenty of movement: the crop follows the action; other shots: speaker"


def test_clip_quality_range():
    good, seen = [shot("single")], [tr(0, 0.4)]
    q = clip_quality(good, seen, 1.0)
    assert 7.0 <= q <= 10.0
    assert clip_quality(good, seen, 2.2) < q
    blurry = [Shot(start_s=0, end_s=30, kind="single", tracks=[0], quality=1.5)]
    assert 0.0 <= clip_quality(blurry, [tr(0, 0.4, score=0.0)], 2.6) <= 4.0
    assert clip_quality([], [], 1.0) == 0.0
