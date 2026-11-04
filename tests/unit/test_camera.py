import numpy as np
import pytest

from clipforge.vision.camera import crop_size, follow, punch_ins, punch_scale, upscale

FPS = 30
T = [round(i / FPS, 6) for i in range(10 * FPS)]
CW, CH = 608, 1080


def run(points, *, cuts=(), switches=(), zoomed=(), crop=(CW, CH), faces=()):
    path = follow(
        points,
        T,
        src_w=1920,
        src_h=1080,
        crop_w=crop[0],
        crop_h=crop[1],
        cuts=cuts,
        switches=switches,
        zoomed=zoomed,
        faces=faces,
    )
    return path.rects


def pts(fx, fy=lambda t: 400.0, until=10.0):
    return [(t, fx(t), fy(t)) for t in (round(i * 0.1, 6) for i in range(100)) if t < until]


def xs(rects):
    return np.array([r[0] for r in rects])


def at(t):
    return round(t * FPS)


def boxed(left, width=350.0):
    """A face ``width`` wide whose left side is at ``left(t)``, eyes in the middle: its points and boxes."""
    spots = pts(lambda t: left(t) + width / 2)
    return spots, [(t, left(t), 250.0, width, 400.0) for t, _, _ in spots]


def held(boxes, t):
    """The box the camera holds at ``t``: the last one seen."""
    return max(b for b in boxes if b[0] <= t + 1e-9)


def walk(t):
    """Round 4's TED close-up: the face's left side still at x 1100, then 350 px left in 0.35 s."""
    return 1100.0 if t < 3 else max(750.0, 1100.0 - 1000 * (t - 3))


def test_stationary_speaker_gets_a_fixed_crop():
    rng = np.random.default_rng(1)
    jitter = {round(i * 0.1, 6): rng.uniform(-30, 30) for i in range(100)}
    assert len(set(run(pts(lambda t: 900 + jitter[t])))) == 1


def test_small_moves_inside_the_dead_zone_hold():
    path = xs(run(pts(lambda t: 500.0 if t < 3 else 804 + (40 if int(t) % 2 else -40))))
    assert len(set(path[at(8) :])) == 1
    assert abs(path[-1] + CW / 2 - 804) <= 45


def test_pan_speed_is_capped():
    path = xs(run(pts(lambda t: 400.0 if t < 2 else 750.0)))
    assert np.abs(np.diff(path)).max() * FPS <= 0.6 * CW + FPS
    assert path[at(2.5)] > path[0]
    assert path[-1] == round(750 - CW / 2)


def test_a_lasting_jump_is_a_hard_cut():
    path = xs(run(pts(lambda t: 300.0 if t < 5 else 1300.0)))
    assert list(np.flatnonzero(np.diff(path))) == [at(5) - 1]
    assert path[0] == 0 and path[-1] == round(1300 - CW / 2)
    blip = xs(run(pts(lambda t: 1700.0 if 8.0 <= t < 8.2 else (300.0 if t < 3 else 700.0))))
    assert len(set(blip[at(7) :])) == 1


def test_a_face_never_leaves_the_crop_where_it_fits():
    """Round 4's TED close-ups and Dream's webcam: a face over half the crop's width slid a quarter or more
    out of it before the camera caught up, and a still speaker who leans takes a big face past a fixed
    crop's side. The followed face's box stays inside the crop wherever it fits, punched in or not."""

    def lean(t):
        return 800.0 + (200.0 if 6 <= t < 6.4 else 0.0)

    for left, zooms in ((walk, ()), (lean, ()), (lean, [(5.5, 7.0, 1.15)])):
        spots, boxes = boxed(left)
        for ti, (x, _, w, _) in zip(T, run(spots, zoomed=zooms, faces=boxes), strict=True):
            _, bx, _, bw, _ = held(boxes, ti)
            assert x <= bx + 0.5 and bx + bw <= x + w + 0.5, (left.__name__, zooms, ti)


@pytest.mark.parametrize("way", [-1, 1])
def test_the_camera_sets_off_in_time_instead_of_being_dragged(way):
    """Offline the camera knows where the face is going: for a move it can make at its own top speed, it
    is under way before the face moves, so the face never pushes it faster, either way."""
    spots, boxes = boxed(walk if way < 0 else lambda t: 1920 - 350 - walk(t))
    path = xs(run(spots, faces=boxes))
    assert np.abs(np.diff(path)).max() * FPS <= 0.6 * CW + FPS
    assert (path[at(3)] - path[0]) * way > 0


def test_the_camera_meets_the_nearer_limit_first():
    """A big face that flicks left and then right faster than the camera can follow: it is in time for
    the first limit, so only the second pushes it."""
    spots, boxes = boxed(lambda t: 650.0 if 5.0 <= t < 5.1 else 950.0 if 5.1 <= t < 5.2 else 800.0, 400.0)
    path = xs(run(spots, faces=boxes))
    assert (np.abs(np.diff(path)) * FPS > 0.6 * CW + FPS).sum() == 1


def test_a_fast_move_just_after_a_cut_is_met_from_the_cut():
    """A shot that opens on a face about to move fast: the camera opens where it can keep up, not on the
    eyes, so the face never pushes it."""

    def opens(t):
        return 300.0 if t < 5 else max(750.0, 1100.0 - 1000 * max(0.0, t - 5.2))

    spots, boxes = boxed(opens)
    path = xs(run(spots, faces=boxes, cuts=[5.0]))
    assert np.abs(np.diff(path[at(5) :])).max() * FPS <= 0.6 * CW + FPS


# Round 4's TED close-up between two cuts, 24 fps: (frame, eye x, box left, box width), held till the next
DART = [
    (0, 1120, 924, 370),
    (2, 1143, 950, 370),
    (5, 1181, 994, 370),
    (7, 1252, 1069, 370),
    (10, 1288, 1112, 370),
    (12, 1306, 1134, 370),
    (14, 1311, 1145, 369),
    (17, 1256, 1102, 365),
    (19, 1156, 1015, 363),
    (22, 1105, 972, 363),
    (24, 1080, 950, 362),
    (26, 1085, 948, 362),
    (29, 1087, 947, 362),
    (31, 1089, 946, 360),
    (34, 1083, 935, 360),
    (36, 1060, 910, 361),
    (38, 1010, 865, 360),
    (41, 978, 834, 358),
    (43, 949, 806, 360),
    (46, 905, 778, 362),
    (48, 869, 741, 362),
    (50, 811, 691, 368),
    (53, 756, 649, 371),
    (55, 705, 596, 367),
    (58, 680, 569, 365),
    (60, 667, 556, 375),
    (62, 710, 589, 372),
    (65, 780, 644, 378),
    (67, 865, 714, 381),
    (70, 952, 779, 382),
    (72, 1023, 843, 379),
    (74, 1138, 936, 378),
]


def test_a_nudge_back_does_not_make_the_camera_late_for_a_dart():
    """The face drifts left, then darts right faster than the camera. Its box nudges the camera back a few
    pixels just as the camera sets off for the dart; the nudge must not slow the camera down, so the dart
    shoves it less than twice its top speed."""
    t = [k / 24 for k in range(76)]
    held = [max(r for r in DART if r[0] <= k) for k in range(76)]
    spots = [(t[k], ex, 400.0) for k, (_, ex, _, _) in enumerate(held)]
    boxes = [(t[k], bx, 250.0, bw, 400.0) for k, (_, _, bx, bw) in enumerate(held)]
    path = xs(follow(spots, t, src_w=1920, src_h=1080, crop_w=CW, crop_h=CH, faces=boxes).rects)
    assert np.abs(np.diff(path)).max() * 24 < 2 * 0.6 * CW


def test_keeping_the_face_in_never_reaches_across_a_jump():
    """A lasting jump with its face boxes is still one hard cut, with no drift before it, and a blip
    still moves nothing."""
    spots, boxes = boxed(lambda t: 200.0 if t < 5 else 1200.0, width=200.0)
    assert list(np.flatnonzero(np.diff(xs(run(spots, faces=boxes))))) == [at(5) - 1]
    spots, boxes = boxed(lambda t: 1600.0 if 8.0 <= t < 8.2 else (200.0 if t < 3 else 600.0), width=200.0)
    assert len(set(xs(run(spots, faces=boxes))[at(7) :])) == 1


def test_no_pan_across_a_scene_cut():
    path = xs(run(pts(lambda t: 400.0 if t < 5 else 700.0), cuts=[5.0]))
    assert set(path[: at(5)]) == {round(400 - CW / 2)}
    assert set(path[at(5) :]) == {round(700 - CW / 2)}


def test_eyes_sit_at_38_percent():
    rect = run(pts(lambda t: 900.0, lambda t: 500.0), crop=(450, 800))[0]
    assert rect == (675, 196, 450, 800)
    assert (500 - rect[1]) / 800 == pytest.approx(0.38, abs=0.002)


def test_crops_stay_inside_any_frame():
    assert crop_size(1920, 1080, 1080, 1920) == (608, 1080)
    assert crop_size(1080, 1920, 1080, 1920) == (1080, 1920)
    rng = np.random.default_rng(3)
    wild = {round(i * 0.1, 6): (rng.uniform(-200, 2100), rng.uniform(-100, 1200)) for i in range(100)}
    zooms = [(1.0, 3.0, 1.15), (5.0, 7.0, 1.08)]
    for crop in ((CW, CH), (450, 800)):
        rects = run(
            pts(lambda t: wild[t][0], lambda t: wild[t][1]),
            cuts=[3.0, 6.5],
            switches=[8.0],
            zoomed=zooms,
            crop=crop,
        )
        assert len(rects) == len(T)
        assert all(
            x >= 0 and x + w <= 1920 and y >= 0 and y + h <= 1080 and w > 0 and h > 0 for x, y, w, h in rects
        )
    assert run([])[0] == (656, 0, CW, CH)
    for sw, sh in ((1920, 1080), (1440, 1080), (1080, 1920), (3840, 2160), (1279, 719)):
        cw, ch = crop_size(sw, sh, 1080, 1920)
        spots = [(t, x * sw / 1920, y * sh / 1080) for t, (x, y) in sorted(wild.items())]
        got = follow(spots, T, src_w=sw, src_h=sh, crop_w=cw, crop_h=ch, cuts=[3.0], zoomed=zooms).rects
        assert all(x >= 0 and x + w <= sw and y >= 0 and y + h <= sh for x, y, w, h in got)


def test_camera_holds_then_centres_when_the_face_is_lost():
    path = xs(run(pts(lambda t: 600.0, until=4.0)))
    assert set(path[: at(5)]) == {296}
    assert set(path[at(9) :]) == {656}
    assert (np.diff(path) >= 0).all()


def test_punch_ins_are_spaced_and_long_enough():
    got = punch_ins([0, 1, 2.5, 4.2, 5, 9, 9.5, 14], 16.0, scale=1.08)
    assert got == [(0, 2.5, 1.08), (4.2, 9, 1.08), (9.5, 14, 1.08)]
    for (a, b, _), (c, _, _) in zip(got, got[1:], strict=False):
        assert c - a >= 4.0 and c > b
    assert all(b - a >= 2.0 for a, b, _ in got)
    assert punch_scale(1080) == 1.08 and punch_scale(2160) == 1.15
    zoomed = run(pts(lambda t: 900.0), zoomed=got)
    assert zoomed[at(1)][2:] == (round(CW / 1.08), round(CH / 1.08)) and zoomed[at(3)][2:] == (CW, CH)


def test_punch_ins_can_end_on_any_sentence_start():
    got = punch_ins([3.0, 7.5], 16.0, scale=1.08, stops=[0.0, 3.0, 5.5, 7.5, 12.0])
    assert got == [(3.0, 5.5, 1.08), (7.5, 12.0, 1.08)]


def test_upscale_over_1_9_is_flagged():
    factor, flagged = upscale(608, 1080, 1080, 1920)
    assert factor == pytest.approx(1.78, abs=0.01) and not flagged
    assert upscale(500, 889, 1080, 1920)[1]
