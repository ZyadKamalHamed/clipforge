import numpy as np
import pytest

from clipforge.models import FaceBox, SpeakerTurn, Track
from clipforge.vision.asd import (
    drop_short_turns,
    frame_loudness,
    mouth_activity,
    mouth_patch,
    speaker_turns,
    switch_time,
)

TIMES = [round(i * 0.1, 6) for i in range(100)]


def track(tid, mouth, shot=0, times=TIMES):
    n = len(times)
    return Track(
        id=tid,
        shot=shot,
        times=times,
        boxes=[(0.1 + 0.5 * tid, 0.2, 0.2, 0.3)] * n,
        eyes=[(0.2 + 0.5 * tid, 0.3)] * n,
        scores=[0.9] * n,
        src=[None] * n,
        mouth=mouth,
    )


def mouth(active, seed):
    rng = np.random.default_rng(seed)
    return [float((1.0 if active(t) else 0.1) + rng.uniform(0, 0.05)) for t in TIMES]


LOUD = [1.0] * len(TIMES)


def test_mouth_patch_is_a_small_grey_patch():
    frame = np.random.default_rng(1).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    marks = [(0.45, 0.4), (0.55, 0.4), (0.5, 0.5), (0.46, 0.6), (0.54, 0.6)]
    patch = mouth_patch(frame, FaceBox(t=0, x=0.4, y=0.3, w=0.2, h=0.4, score=0.9, marks=marks))
    assert patch.shape == (16, 32)


def test_mouth_activity_follows_a_moving_mouth():
    rng = np.random.default_rng(2)
    a, b = rng.normal(size=(16, 32)), rng.normal(size=(16, 32))
    moving = {(i, 0): (a if i % 2 else b) for i in range(10)}
    still = {(i, 0): a for i in range(10)}
    tr = track(0, [], times=TIMES[:10]).model_copy(update={"src": [(i, 0) for i in range(10)]})
    assert mouth_activity(tr, moving)[0] == 0.0
    assert np.mean(mouth_activity(tr, moving)) > np.mean(mouth_activity(tr, still)) + 0.5


def test_frame_loudness_follows_the_audio():
    sr = 16000
    t = np.arange(sr * 2) / sr
    pcm = np.where(t < 1.0, 0.5 * np.sin(2 * np.pi * 220 * t), 0.0)
    loud = frame_loudness(pcm, sr, [0.5, 1.5], start_s=0.0)
    assert loud[0] > 0.3 and loud[1] < 0.01


def test_speaker_turns_follow_the_mouth_that_moves_while_audio_is_loud():
    tracks = [track(0, mouth(lambda t: t < 5, 3)), track(1, mouth(lambda t: t >= 5, 4))]
    turns = speaker_turns(tracks, LOUD, TIMES, [])
    assert [t.track for t in turns] == [0, 1]
    assert turns[0].start_s == pytest.approx(0.0)
    assert turns[0].end_s == pytest.approx(5.0, abs=0.4)
    assert turns[1].end_s == pytest.approx(10.0)


def test_quiet_windows_never_switch():
    tracks = [track(0, mouth(lambda t: t < 5, 3)), track(1, mouth(lambda t: t >= 5, 4))]
    quiet = [1.0 if t < 5 else 0.1 for t in TIMES]
    assert [t.track for t in speaker_turns(tracks, quiet, TIMES, [])] == [0]


def test_new_speaker_needs_three_windows():
    b_talks = lambda t: 5.2 <= t < 6.0  # noqa: E731
    tracks = [track(0, mouth(lambda t: not b_talks(t), 3)), track(1, mouth(b_talks, 4))]
    assert [t.track for t in speaker_turns(tracks, LOUD, TIMES, [])] == [0]


def test_turn_under_1_2_s_ignored():
    turns = [SpeakerTurn(start_s=0, end_s=5, track=0), SpeakerTurn(start_s=5, end_s=5.8, track=1)]
    turns.append(SpeakerTurn(start_s=5.8, end_s=10, track=0))
    assert drop_short_turns(turns) == [SpeakerTurn(start_s=0, end_s=10, track=0)]


def test_switch_lands_early_and_snaps_to_a_cut():
    assert switch_time(5.2, []) == pytest.approx(5.05)
    assert switch_time(5.2, [4.8]) == pytest.approx(4.8)
    assert switch_time(5.2, [4.0]) == pytest.approx(5.05)


def test_single_face_shot_has_no_turns():
    assert speaker_turns([track(0, mouth(lambda t: True, 3))], LOUD, TIMES, []) == []
