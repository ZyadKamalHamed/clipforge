import subprocess

import pytest

from clipforge.media.ffmpeg import ffmpeg_bin
from clipforge.vision.decode import ClipSource, analysis_decode
from clipforge.vision.scenes import find_cuts, merge_cuts, parse_scdet


def make(path, *args):
    subprocess.run(
        [str(ffmpeg_bin()), "-hide_banner", "-loglevel", "error", "-y", *args, str(path)], check=True
    )
    return path


@pytest.fixture(scope="module")
def pattern(tmp_path_factory):
    return make(
        tmp_path_factory.mktemp("decode") / "pattern.mp4",
        *("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=8", "-c:v", "libx264", "-pix_fmt", "yuv420p"),
    )


@pytest.fixture(scope="module")
def colours(tmp_path_factory):
    """Red for 3 s, blue for 2 s, green for 3 s: cuts at 3 s and 5 s."""
    inputs = []
    for colour, seconds in (("red", 3), ("blue", 2), ("green", 3)):
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s=640x360:r=30:d={seconds}"]
    return make(
        tmp_path_factory.mktemp("decode") / "colours.mp4",
        *inputs,
        *("-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-c:v", "libx264", "-pix_fmt", "yuv420p"),
    )


@pytest.fixture(scope="module")
def greys(tmp_path_factory):
    """Dark grey for 2 s, then a slightly lighter grey for 2 s: a cut ffmpeg scores near 5, under the 10
    that always counts, like a cut between two shots of a dark stage."""
    inputs = []
    for colour in ("0x202020", "0x343434"):
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s=640x360:r=30:d=2"]
    return make(
        tmp_path_factory.mktemp("decode") / "greys.mp4",
        *inputs,
        *("-filter_complex", "[0][1]concat=n=2:v=1:a=0", "-c:v", "libx264", "-pix_fmt", "yuv420p"),
    )


def collect(src, start, end):
    frames = []
    decoded = analysis_decode(src, start, end, lambda i, t, frame: frames.append((i, t, frame.shape)))
    return decoded, frames


def test_analysis_decode_frames_and_times(pattern):
    decoded, frames = collect(ClipSource(pattern), 2.0, 6.0)
    assert 39 <= len(frames) <= 41
    assert [f[0] for f in frames[:3]] == [0, 1, 2]
    assert frames[0][1] == pytest.approx(2.0) and frames[1][1] == pytest.approx(2.1)
    assert frames[0][2] == (360, 640, 3)
    assert decoded.times == [f[1] for f in frames]
    assert (decoded.geometry.width, decoded.geometry.height) == (1280, 720)
    assert decoded.geometry.fps == pytest.approx(30.0)


def test_scene_cuts_found(colours):
    decoded, _ = collect(ClipSource(colours), 0.0, 8.0)
    assert len(decoded.cuts) == 2
    assert decoded.cuts[0] == pytest.approx(3.0, abs=0.1)
    assert decoded.cuts[1] == pytest.approx(5.0, abs=0.1)


def test_section_file_reports_source_times(colours):
    decoded, frames = collect(ClipSource(colours, offset_s=600.0), 600.0, 608.0)
    assert frames[0][1] == pytest.approx(600.0)
    assert decoded.cuts[0] == pytest.approx(603.0, abs=0.1)


def test_quiet_cut_found(greys):
    decoded, _ = collect(ClipSource(greys), 0.0, 4.0)
    assert decoded.cuts == [pytest.approx(2.0, abs=0.1)]


def scores_text(scores, fps=30.0):
    """What ffmpeg's metadata filter prints for scdet's score of each frame."""
    return "".join(
        f"frame:{i} pts:{i} pts_time:{i / fps:g}\nlavfi.scd.score={v:.3f}\n" for i, v in enumerate(scores)
    )


def test_parse_scdet_adds_the_offset():
    scores = [0.05] * 89 + [40.0] + [0.05] * 30
    assert parse_scdet(scores_text(scores), offset_s=600.0) == [pytest.approx(602.96667)]


def at(fps, scores):
    return [i / fps for i in range(len(scores))], scores


def test_find_cuts_counts_a_lone_spike_in_still_footage():
    scores = [0.05] * 48 + [4.5] + [0.05] * 48  # 24 fps, a cut between two similar shots at 2 s
    assert find_cuts(*at(24.0, scores)) == [pytest.approx(2.0)]


def test_find_cuts_counts_a_big_change_anywhere():
    busy = [1.0, 2.5, 0.5, 2.0, 1.5, 3.0] * 20
    busy[60] = 12.0
    assert find_cuts(*at(60.0, busy)) == [pytest.approx(1.0)]


def test_find_cuts_ignores_bursts_and_small_spikes_in_busy_footage():
    busy = [0.3, 0.6, 0.2, 0.5] * 30  # 60 fps gameplay, a level near 0.4
    busy[30:33] = [8.4, 2.9, 6.0]  # a burst of change, like an explosion
    busy[90] = 5.4  # a lone change only about 13 times the level around it
    assert find_cuts(*at(60.0, busy)) == []


def test_merge_cuts():
    assert merge_cuts([5.0, 1.0, 1.3, 2.0, 5.39]) == [1.0, 2.0, 5.0]


def test_decode_rotated_video_comes_out_upright(tmp_path):
    flat = make(
        tmp_path / "flat.mp4",
        *("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p"),
    )
    turned = make(tmp_path / "turned.mp4", "-display_rotation", "90", "-i", str(flat), "-c", "copy")
    decoded, frames = collect(ClipSource(turned), 0.0, 1.0)
    assert (decoded.geometry.width, decoded.geometry.height) == (360, 640)
    assert frames[0][2] == (640, 360, 3)
