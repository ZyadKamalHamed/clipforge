import numpy as np
import pytest

from clipforge.models import FaceBox
from clipforge.vision.faces import FaceDetector
from clipforge.vision.tracks import build_tracks


class StubNet:
    def __init__(self, rows):
        self.rows = rows
        self.sizes = []

    def setInputSize(self, size):  # noqa: N802 (OpenCV's name)
        self.sizes.append(size)

    def detect(self, frame):
        return 1, self.rows


def face(t, x, y=0.3, w=0.1, h=0.2):
    marks = [(x + 0.3 * w, y + 0.4 * h), (x + 0.7 * w, y + 0.4 * h), (x + 0.5 * w, y + 0.6 * h)]
    marks += [(x + 0.35 * w, y + 0.8 * h), (x + 0.65 * w, y + 0.8 * h)]
    return FaceBox(t=t, x=x, y=y, w=w, h=h, score=0.9, marks=marks)


def samples(times, xs_per_time):
    return [(t, [face(t, x) for x in xs]) for t, xs in zip(times, xs_per_time, strict=True)]


def grid(start, end, step=0.1):
    return [round(start + i * step, 6) for i in range(int(round((end - start) / step)) + 1)]


def test_detector_normalises_boxes():
    row = [64, 36, 128, 72, 100, 60, 150, 60, 125, 80, 105, 95, 145, 95, 0.9]
    net = StubNet(np.array([row], dtype=np.float32))
    found = FaceDetector(None, net=net).detect(np.zeros((360, 640, 3), np.uint8), 1.5)
    assert net.sizes == [(640, 360)]
    (box,) = found
    assert (box.t, box.score) == (1.5, pytest.approx(0.9))
    assert (box.x, box.y, box.w, box.h) == pytest.approx((0.1, 0.1, 0.2, 0.2))
    assert box.marks[0] == pytest.approx((100 / 640, 60 / 360))
    assert len(box.marks) == 5


def test_detector_no_faces():
    assert FaceDetector(None, net=StubNet(None)).detect(np.zeros((360, 640, 3), np.uint8), 0.0) == []


def test_tracks_link_by_iou_within_a_shot():
    times = grid(0.0, 1.9)
    tracks = build_tracks(samples(times, [[0.40 + 0.002 * i] for i in range(len(times))]), [])
    (track,) = tracks
    assert track.times == times
    assert track.shot == 0
    assert len(track.boxes) == len(times) == len(track.eyes) == len(track.src)


def test_tracks_split_at_a_cut():
    times = grid(0.0, 1.9)
    tracks = build_tracks(samples(times, [[0.4]] * len(times)), [1.0])
    assert [(t.shot, t.times[0], t.times[-1]) for t in tracks] == [(0, 0.0, 0.9), (1, 1.0, 1.9)]


def test_tracks_bridge_gaps_up_to_0_6_s():
    times = grid(0.0, 2.5)
    seen = [[0.4] if t <= 1.0 or t >= 1.6 else [] for t in times]
    (track,) = build_tracks(samples(times, seen), [])
    assert track.times == times
    gap = [src for t, src in zip(track.times, track.src, strict=True) if 1.0 < t < 1.6]
    assert gap and all(src is None for src in gap)

    seen = [[0.4] if t <= 1.0 or t >= 1.8 else [] for t in times]
    assert len(build_tracks(samples(times, seen), [])) == 2


def test_tracks_shorter_than_0_4_s_dropped():
    times = grid(0.0, 0.3)
    assert build_tracks(samples(times, [[0.4]] * len(times)), []) == []


def test_two_faces_make_two_tracks():
    times = grid(0.0, 1.0)
    tracks = build_tracks(samples(times, [[0.2, 0.7]] * len(times)), [])
    assert sorted(round(t.boxes[0][0], 2) for t in tracks) == [0.2, 0.7]
    assert {t.id for t in tracks} == {0, 1}
