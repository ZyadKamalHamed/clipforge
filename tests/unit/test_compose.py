"""Layouts, the compositor and the encoder (SPEC 7.7 and 7.10)."""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from clipforge.errors import ToolError
from clipforge.media.ffmpeg import ffmpeg_bin, ffprobe_bin, run
from clipforge.render import encode
from clipforge.render.audio import SR, assemble, decode_audio, finish
from clipforge.render.captions import CaptionPage, PageWord
from clipforge.render.cards import card_sprite
from clipforge.render.compositor import compose, overlays, paint
from clipforge.render.edl import EDL, GAMEPLAY_BOX, H_OUT, PANELS, W_OUT, Span, fit_box
from clipforge.render.encode import (
    Encoder,
    decode_frames,
    encoder_args,
    mux,
    peak_bitrate,
    peak_rate,
    reencode_capped,
    render_video,
    runs,
)
from clipforge.render.hook import credit_sprite, hook_sprite
from clipforge.render.layouts import Panel, cover_size, panels_for, scaled
from clipforge.render.styles import Style
from clipforge.render.timeline import Segment, Timeline
from clipforge.vision.decode import ClipSource
from tests.helpers import make_test_video, video_from_frames, write_wav
from tests.unit.test_overlays import png

RGB_BARS = [
    (255, 255, 255),
    (255, 255, 0),
    (0, 255, 255),
    (0, 255, 0),
    (255, 0, 255),
    (255, 0, 0),
    (0, 0, 255),
    (0, 0, 0),
    (40, 90, 200),
    (128, 128, 128),
]


def bars(w, h):
    """RGB_BARS as upright bars across a w x h BGR frame."""
    frame = np.zeros((h, w, 3), np.uint8)
    step = w // len(RGB_BARS)
    for i, (r, g, b) in enumerate(RGB_BARS):
        frame[:, i * step : (i + 1) * step] = (b, g, r)
    return frame


def bar_errors(frame):
    """How far the middle of each bar is from its colour, in 8 bit steps."""
    h, w = frame.shape[:2]
    step = w // len(RGB_BARS)
    out = []
    for i, (r, g, b) in enumerate(RGB_BARS):
        middle = frame[h // 8 : -h // 8, i * step + step // 4 : (i + 1) * step - step // 4]
        out.append(round(float(np.abs(middle.reshape(-1, 3).mean(axis=0) - (b, g, r)).max())))
    return out


def coded(k, size=(320, 180)):
    """A frame of one colour that spells ``k`` (0 to 511), three bits a channel in steps of 32."""
    w, h = size
    return np.full((h, w, 3), [16 + 32 * (k >> s & 7) for s in (0, 3, 6)], np.uint8)


def code_of(pixels):
    digits = np.clip(np.round((pixels.reshape(-1, 3).mean(axis=0) - 16) / 32), 0, 7).astype(int)
    return int(digits[0] + 8 * digits[1] + 64 * digits[2])


def streams(path):
    out = run([ffprobe_bin(), "-v", "error", "-show_streams", "-of", "json", str(path)]).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def keyframes(path):
    args = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags"]
    packets = json.loads(run([*args, "-of", "json", str(path)]).stdout)["packets"]
    return sorted(float(p["pts_time"]) for p in packets if "K" in p["flags"])


def mp4_boxes(data, start=0, end=None):
    """The MP4 boxes laid end to end from ``start``: (type, offset, size)."""
    out, i, end = [], start, len(data) if end is None else end
    while i + 8 <= end:
        size, kind = int.from_bytes(data[i : i + 4], "big"), data[i + 4 : i + 8].decode("latin-1")
        if size == 1:
            size = int.from_bytes(data[i + 8 : i + 16], "big")
        elif size == 0:
            size = end - i
        out.append((kind, i, size))
        i += size
    return out


def edit_lists(data):
    """How many tracks carry an edit list."""
    moov = next((i, n) for kind, i, n in mp4_boxes(data) if kind == "moov")
    traks = [(i, n) for kind, i, n in mp4_boxes(data, moov[0] + 8, sum(moov)) if kind == "trak"]
    return sum(any(kind == "edts" for kind, _, _ in mp4_boxes(data, i + 8, i + n)) for i, n in traks)


def chain(ranges, fps=30.0):
    """Segments playing the source ranges (start, end, speed) one after another."""
    out, frames = [], 0
    for a, b, speed in ranges:
        n = round((b - a) / speed * fps)
        out.append(Segment(src_start=a, src_end=a + n * speed / fps, out_start=frames / fps, speed=speed))
        frames += n
    return out


def fit_span(n, size, fps=30.0):
    w, h = size
    return Span(start=0.0, end=n / fps, layout="fit", boxes=[fit_box(w, h)], crops=[[(0, 0, w, h)] * n])


def edl_for(segments, spans, *, fps=30.0, src=(1280, 720), style=None, **layers):
    return EDL(
        clip_id="c1",
        fps=fps,
        src_width=src[0],
        src_height=src[1],
        duration=Timeline(segments, fps).duration,
        segments=segments,
        spans=spans,
        style=style or Style(),
        **layers,
    )


def pairs(args):
    return list(zip(args, args[1:], strict=False))


def gradient(w, h):
    """Blue climbing from left to right and green from top to bottom."""
    x = np.repeat(np.linspace(0, 255, w)[None, :], h, axis=0)
    y = np.repeat(np.linspace(0, 255, h)[:, None], w, axis=1)
    return np.dstack([x, y, np.full((h, w), 128.0)]).round().astype(np.uint8)


# The encoder


@pytest.mark.parametrize("name", ["fast", "best"])
def test_colour_bars_round_trip_in_bt709(tmp_path, name):
    out = tmp_path / f"bars_{name}.mp4"
    enc = Encoder(out, fps=30.0, encoder=name)
    for _ in range(10):
        enc.write(bars(W_OUT, H_OUT))
    enc.close()
    (frame,) = decode_frames(ClipSource(out), 0.0, 1, fps=30.0, size=(W_OUT, H_OUT))
    assert max(bar_errors(frame)) <= 6
    v = streams(out)["video"]
    tags = (v["color_range"], v["color_space"], v["color_transfer"], v["color_primaries"])
    assert tags == ("tv", "bt709", "bt709", "bt709")


@pytest.mark.parametrize(
    ("size", "matrix"),
    [((1280, 720), "bt709"), ((1280, 544), "bt709"), ((960, 720), "bt709"), ((640, 360), "bt601")],
)
def test_untagged_sources_read_as_players_read_them(tmp_path, size, matrix):
    """An untagged HD video is taken as BT.709 and an untagged smaller one as BT.601, as players do."""
    path = video_from_frames(tmp_path / "untagged.mp4", [bars(*size)] * 3, matrix=matrix, tagged=False)
    assert streams(path)["video"].get("color_space", "unknown") == "unknown"
    (frame,) = decode_frames(ClipSource(path), 0.0, 1, fps=30.0, size=size)
    assert max(bar_errors(frame)) <= 6


def pq(nits: float) -> float:
    """The PQ signal for ``nits`` (SMPTE ST 2084)."""
    m1, m2, c1, c2, c3 = 2610 / 16384, 2523 / 32, 3424 / 4096, 2413 / 128, 2392 / 128
    y = (nits / 10000) ** m1
    return ((c1 + c2 * y) / (1 + c3 * y)) ** m2


def hdr_video(path, trc: str, columns) -> Path:
    """A 256 x 144 10 bit HEVC file tagged BT.2020 and ``trc``, one column per (R', G', B') in ``columns``,
    each signal 0 to 1 in BT.2020."""
    w, h, n = 256, 144, len(columns)
    planes = [np.zeros((h, w)), np.zeros((h // 2, w // 2)), np.zeros((h // 2, w // 2))]
    for i, (r, g, b) in enumerate(columns):
        y = 0.2627 * r + 0.678 * g + 0.0593 * b
        values = (64 + 876 * y, 512 + 896 * (b - y) / 1.8814, 512 + 896 * (r - y) / 1.4746)
        for plane, v in zip(planes, values, strict=True):
            cw = plane.shape[1] // n
            plane[:, i * cw : (i + 1) * cw] = round(v)
    frame = b"".join(plane.astype("<u2").tobytes() for plane in planes)
    tags = ["-color_primaries", "bt2020", "-color_trc", trc, "-colorspace", "bt2020nc", "-color_range", "tv"]
    args = [ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p10le", "-s", f"{w}x{h}"]
    args += [*tags, "-i", "pipe:0", "-c:v", "libx265", "-crf", "4", "-pix_fmt", "yuv420p10le", *tags]
    args += ["-x265-params", f"log-level=error:colorprim=bt2020:transfer={trc}:colormatrix=bt2020nc"]
    subprocess.run([*args, str(path)], input=frame * 3, check=True)
    return path


@pytest.mark.parametrize(
    ("trc", "white", "grey"), [("smpte2084", pq(203), pq(50)), ("arib-std-b67", 0.75, 0.5)]
)
def test_hdr_sources_read_in_sdr_levels(tmp_path, trc, white, grey):
    """A PQ or HLG video is read in SDR: its reference white as white and a mid grey where BT.2408 puts it,
    not the dull greys its signal gives read as SDR (SPEC 7.2)."""
    path = hdr_video(tmp_path / "hdr.mp4", trc, [(white,) * 3, (grey,) * 3])
    (frame,) = decode_frames(ClipSource(path), 0.0, 1, fps=30.0, size=(64, 36))
    assert frame[18, 16].min() >= 245
    assert np.abs(frame[18, 48].astype(int) - 142).max() <= 8


def test_hdr_sources_keep_their_colours(tmp_path):
    """BT.709 red stored in BT.2020 PQ is read as red, not the pale red its numbers give read as BT.709."""
    path = hdr_video(tmp_path / "red.mp4", "smpte2084", [[pq(203 * c) for c in (0.6274, 0.0691, 0.0164)]])
    (frame,) = decode_frames(ClipSource(path), 0.0, 1, fps=30.0, size=(64, 36))
    b, g, r = frame[18, 32].astype(int)
    assert r >= 235 and max(g, b) <= 30


def test_encoder_args_carry_the_spec_flags():
    fast = encoder_args("fast", 30.0)
    for pair in [
        ("-c:v", "h264_videotoolbox"),
        ("-q:v", "65"),
        ("-profile:v", "high"),
        ("-allow_sw", "0"),
        ("-pix_fmt", "yuv420p"),
        ("-g", "60"),
        ("-keyint_min", "60"),
        ("-flags", "+cgop"),
        ("-colorspace", "bt709"),
        ("-color_primaries", "bt709"),
        ("-color_trc", "bt709"),
        ("-color_range", "tv"),
        ("-movflags", "+faststart"),
        ("-use_editlist", "0"),
    ]:
        assert pair in pairs(fast)
    vf = fast[fast.index("-vf") + 1]
    assert "scale=out_color_matrix=bt709:out_range=tv" in vf and "format=yuv420p" in vf
    assert "color_primaries=bt709" in vf and "color_trc=bt709" in vf
    best = encoder_args("best", 29.97)
    for pair in [
        ("-c:v", "libx264"),
        ("-preset", "medium"),
        ("-crf", "20"),
        ("-maxrate", "16M"),
        ("-bufsize", "32M"),
        ("-profile:v", "high"),
        ("-bf", "0"),
        ("-g", "60"),
        ("-flags", "+cgop"),
        ("-use_editlist", "0"),
    ]:
        assert pair in pairs(best)
    assert ("-g", "120") in pairs(encoder_args("best", 59.94))
    assert ("-g", "48") in pairs(encoder_args("fast", 23.976))
    with pytest.raises(ValueError):
        encoder_args("slow", 30.0)


@pytest.fixture
def started(monkeypatch):
    """Every process the encoder module starts."""
    made, real = [], subprocess.Popen

    def recording(*args, **kwargs):
        proc = real(*args, **kwargs)
        made.append(proc)
        return proc

    monkeypatch.setattr(encode.subprocess, "Popen", recording)
    return made


def left_over(before):
    """The threads started since ``before`` that are still running."""
    return [t for t in threading.enumerate() if t not in before]


def test_encoder_failure_is_a_tool_error(tmp_path):
    before = threading.enumerate()
    enc = Encoder(tmp_path / "missing" / "video.mp4", fps=30.0)
    with pytest.raises(ToolError) as err:  # from a write: no more frames go to an encoder that has died
        for _ in range(30):
            enc.write(bars(W_OUT, H_OUT))
    assert err.value.code == "encode_failed" and "missing" in (err.value.details or "")
    enc.kill()
    assert left_over(before) == []


def stuck(tmp_path, monkeypatch):
    """Makes ffmpeg a program that never reads, writes or exits, and STALL_S short."""
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nexec sleep 600\n")
    fake.chmod(0o755)
    monkeypatch.setattr(encode, "ffmpeg_bin", lambda: str(fake))
    monkeypatch.setattr(encode, "STALL_S", 0.5)


@contextlib.contextmanager
def deadline(procs, s=10.0):
    """Kills ``procs`` after ``s`` seconds, so code that would wait on them for ever fails instead."""
    timer = threading.Timer(s, lambda: [p.kill() for p in procs if p.poll() is None])
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        timer.join()


def test_an_encoder_that_stops_taking_frames_times_out(tmp_path, started, monkeypatch):
    before = threading.enumerate()
    stuck(tmp_path, monkeypatch)
    frame = bars(W_OUT, H_OUT)
    with deadline(started):
        enc = Encoder(tmp_path / "a.mp4", fps=30.0)
        with pytest.raises(ToolError) as err:  # its socket and queue fill, then a write waits STALL_S
            for _ in range(30):
                enc.write(frame)
        assert err.value.code == "encode_timeout"
        enc = Encoder(tmp_path / "b.mp4", fps=30.0)
        for _ in range(3):  # more than its socket holds, fewer than its queue
            enc.write(frame)
        with pytest.raises(ToolError) as err:
            enc.close()
        assert err.value.code == "encode_timeout"
    assert len(started) == 2 and all(p.poll() is not None for p in started)
    assert left_over(before) == []


def test_a_decoder_that_sends_nothing_times_out(tmp_path, started, monkeypatch):
    before = threading.enumerate()
    src = ClipSource(video_from_frames(tmp_path / "part.mp4", (coded(k) for k in range(10))), offset_s=0.0)
    stuck(tmp_path, monkeypatch)
    with deadline(started), pytest.raises(ToolError) as err:
        list(decode_frames(src, 0.0, 5, fps=30.0, size=(64, 36)))
    assert err.value.code == "decode_timeout"
    assert all(p.poll() is not None for p in started) and left_over(before) == []


def test_decode_frames_reads_a_section_download_on_source_time(tmp_path):
    """A section download starting 1 s into the video: source time t is file time t - 1, and frames asked
    for from before the file starts hold its first frame."""
    src = ClipSource(video_from_frames(tmp_path / "part.mp4", (coded(k) for k in range(60))), offset_s=1.0)
    assert [code_of(f) for f in decode_frames(src, 1.5, 10, fps=30.0, size=(64, 36))] == list(range(15, 25))
    early = [code_of(f) for f in decode_frames(src, 0.5, 30, fps=30.0, size=(64, 36))]
    assert early == [0] * 15 + list(range(15))
    late = [code_of(f) for f in decode_frames(src, 2.8, 10, fps=30.0, size=(64, 36))]
    assert late == [54, 55, 56, 57, 58] + [59] * 5  # the file ends: its last frame holds
    with pytest.raises(ToolError) as err:
        list(decode_frames(src, 9.0, 3, fps=30.0, size=(64, 36)))
    assert err.value.code == "decode_failed"


# Layouts


def test_panels_for_every_layout():
    crop = (656, 0, 608, 1080)
    full = (0, 0, W_OUT, H_OUT)
    assert panels_for("face", 1920, 1080, [crop]) == [Panel(full, crop)]
    assert panels_for("motion", 1920, 1080, [crop]) == [Panel(full, crop)]
    a, b = (100, 0, 1000, 889), (900, 100, 1000, 889)
    assert panels_for("stacked", 1920, 1080, [a, b]) == [
        Panel((0, 0, W_OUT, 960), a),
        Panel((0, 960, W_OUT, 960), b),
    ]
    face, game = (300, 40, 1080, 640), (480, 0, 960, 1080)
    assert panels_for("stream", 1920, 1080, [face, game]) == [
        Panel((0, 0, W_OUT, 640), face),
        Panel((0, 640, W_OUT, 1280), game),
    ]
    speaker = (514, 0, 1104, 1080)
    assert panels_for("gameplay", 1920, 1080, [speaker]) == [Panel((0, 0, W_OUT, 1056), speaker)]
    assert panels_for("gameplay", 1920, 1080, [speaker], gameplay=(1536, 864)) == [
        Panel((0, 0, W_OUT, 1056), speaker),
        Panel(GAMEPLAY_BOX, (228, 0, 1080, 864), source="gameplay"),
    ]
    back, front = panels_for("fit", 1920, 1080)
    assert front == Panel((0, 657, W_OUT, 606), (0, 0, 1920, 1080))
    assert back.box == full and back.blurred and back.source == "main"
    x, y, w, h = back.crop  # the middle of the frame, a little tighter than the widest 9:16 crop
    assert (w, h) == (552, 980) and (x + w / 2, y + h / 2) == (960, 540)
    assert panels_for("fit", 1080, 1920) == [Panel(full, (0, 0, 1080, 1920))]


def test_cover_size_keeps_the_shape():
    assert cover_size(320, 180, 1080, 864) == (1536, 864)
    assert cover_size(1920, 1080, 1080, 864) == (1536, 864)
    assert cover_size(1080, 1920, 1080, 864) == (1080, 1920)


def test_scaled_picks_its_kernel_by_the_scale():
    part = np.random.default_rng(3).integers(0, 256, (1080, 1215, 3), dtype=np.uint8)
    assert scaled(part, 1215, 1080) is part
    for (w, h), kernel in (
        ((2160, 1920), cv2.INTER_LANCZOS4),  # enlarged
        ((1080, 960), cv2.INTER_CUBIC),  # shrunk to 0.89, where cubic measured sharper than area averaging
        ((984, 874), cv2.INTER_CUBIC),  # to 0.81
        ((958, 852), cv2.INTER_AREA),  # to 0.79, where area averaging measured sharper
        ((540, 480), cv2.INTER_AREA),
    ):
        assert np.array_equal(scaled(part, w, h), cv2.resize(part, (w, h), interpolation=kernel)), (w, h)


def test_compose_face_layout_fills_the_canvas():
    frame = gradient(1280, 720)
    span = Span(
        start=0.0,
        end=1.0,
        layout="face",
        boxes=PANELS["face"],
        crops=[[(438, 0, 404, 720), (0, 0, 404, 720)]],
    )
    canvas = compose(frame, span, 0).astype(int)
    assert canvas.shape == (H_OUT, W_OUT, 3)
    assert abs(canvas[:, 2, 0].mean() - 255 * 439 / 1279) <= 3  # the crop's left edge is the canvas's
    assert abs(canvas[:, -3, 0].mean() - 255 * 841 / 1279) <= 3
    assert canvas[2, :, 1].mean() <= 3 and canvas[-3, :, 1].mean() >= 252
    later = compose(frame, span, 1).astype(int)  # the crop of the span's second frame
    assert abs(later[:, 2, 0].mean() - 255 * 1 / 1279) <= 3
    assert np.array_equal(compose(frame, span, 10), compose(frame, span, 1))  # past the end: its last crop
    past = Span(start=0.0, end=1.0, layout="face", boxes=PANELS["face"], crops=[[(1000, 0, 404, 720)]])
    edge = Span(start=0.0, end=1.0, layout="face", boxes=PANELS["face"], crops=[[(876, 0, 404, 720)]])
    assert np.array_equal(compose(frame, past, 0), compose(frame, edge, 0))  # moved inside the frame


def test_compose_stacked_and_gameplay_panels():
    blue, red, green = (255, 0, 0), (0, 0, 255), (0, 255, 0)
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[:, :640], frame[:, 640:] = blue, red
    crops = [[(40, 0, 540, 480)], [(700, 200, 540, 480)]]
    canvas = compose(
        frame, Span(start=0.0, end=1.0, layout="stacked", boxes=PANELS["stacked"], crops=crops), 0
    )
    assert np.abs(canvas[:960].astype(int) - blue).max() <= 1
    assert np.abs(canvas[960:].astype(int) - red).max() <= 1
    talk = np.full((720, 1280, 3), blue, np.uint8)
    game = np.full((864, 1536, 3), green, np.uint8)
    span = Span(start=0.0, end=1.0, layout="gameplay", boxes=PANELS["gameplay"], crops=[[(272, 0, 736, 720)]])
    canvas = compose(talk, span, 0, gameplay_frame=game)
    assert np.abs(canvas[:1056].astype(int) - blue).max() <= 1
    assert np.array_equal(canvas[1056:], np.broadcast_to(np.uint8(green), (864, W_OUT, 3)))
    bare = compose(talk, span, 0)
    assert not bare[1056:].any()  # no gameplay frame: its box stays black


def test_compose_fit_has_a_blurred_background():
    frame = np.random.default_rng(1).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
    span = Span(start=0.0, end=1.0, layout="fit", boxes=[fit_box(1280, 720)], crops=[[(0, 0, 1280, 720)]])
    canvas = compose(frame, span, 0).astype(float)
    _, y, _, h = fit_box(1280, 720)
    middle, above, below = canvas[y : y + h], canvas[:y], canvas[y + h :]

    def sharpness(a):
        return np.abs(np.diff(a, axis=1)).mean()

    assert sharpness(middle) > 30
    for band in (above, below):
        assert sharpness(band) < 1  # blurred
        assert 0.4 * frame.mean() < band.mean() < 0.8 * frame.mean()  # darkened, not black


def test_compose_vertical_source_fills_canvas():
    tall = np.random.default_rng(2).integers(0, 256, (H_OUT, W_OUT, 3), dtype=np.uint8)
    for layout, boxes in (("face", PANELS["face"]), ("fit", [fit_box(W_OUT, H_OUT)])):
        span = Span(start=0.0, end=1.0, layout=layout, boxes=boxes, crops=[[(0, 0, W_OUT, H_OUT)]])
        assert np.array_equal(compose(tall, span, 0), tall)
    small = np.full((1280, 720, 3), 200, np.uint8)
    span = Span(start=0.0, end=1.0, layout="fit", boxes=[fit_box(720, 1280)], crops=[[(0, 0, 720, 1280)]])
    assert compose(small, span, 0).min() >= 198  # no black band anywhere


# Overlays


def test_overlays_follow_the_edl_times(tmp_path):
    style = Style()
    hook = hook_sprite("Nobody tells you this", "Nobody", style)
    credit = credit_sprite("Source: Test Channel", 400 + hook.h + 12)
    card = card_sprite("Sharks are older than trees")
    fire = tmp_path / "fire.png"
    fire.write_bytes(png())
    page = CaptionPage(
        start=0.0,
        end=3.0,
        words=[PageWord(text="hello", start=0.0, end=1.0), PageWord(text="there", start=1.0, end=3.0)],
    )
    edl = edl_for(
        chain([(0.0, 5.0, 1.0)]),
        [fit_span(150, (1280, 720))],
        pages=[page],
        hook={
            "title": "Nobody tells you this",
            "accent_word": "Nobody",
            "start": 0.0,
            "end": 4.0,
            "scale": 1.0,
            "box": [hook.x, 400, hook.w, hook.h],
            "covers": None,
        },
        credit={"text": "Source: Test Channel", "start": 0.0, "end": 4.0, "box": list(credit.box)},
        cards=[
            {
                "text": "Sharks are older than trees",
                "sid": "s1",
                "start": 1.0,
                "end": 3.5,
                "box": [card.x, 700, card.w, card.h],
            }
        ],
        emoji=[
            {
                "emoji": "x",
                "word": "hello",
                "start": 0.2,
                "end": 1.4,
                "box": [480, 900, 120, 120],
                "size": 120,
            },
            {
                "emoji": "x",
                "word": "there",
                "start": 2.0,
                "end": 3.2,
                "box": [480, 900, 120, 120],
                "size": 120,
                "png": str(fire),
            },
        ],
    )
    early = overlays(edl, 0.5)  # the caption, the credit and the hook; the emoji with no picture is left out
    assert [a for _, a in early] == [1.0, 1.0, 1.0]
    assert early[1][0].box == credit.box and early[2][0].box == (hook.x, 400, hook.w, hook.h)
    middle = overlays(edl, 2.01)  # the caption, the emoji popping in, the card, the credit and the hook
    assert len(middle) == 5
    pop = middle[1][0]
    assert pop.w < 120 and (pop.x + pop.w / 2, pop.y + pop.h / 2) == pytest.approx((540, 960), abs=1)
    assert middle[2][0].box == (card.x, 700, card.w, card.h)
    late = overlays(edl, 3.9)  # the hook and the credit fading out together
    assert [s.box for s, _ in late] == [credit.box, (hook.x, 400, hook.w, hook.h)]
    assert [a for _, a in late] == pytest.approx([0.5, 0.5])
    assert overlays(edl, 4.0) == []
    canvas = np.zeros((H_OUT, W_OUT, 3), np.uint8)
    paint(canvas, edl, 0.5)
    assert canvas[400 : 400 + hook.h, hook.x : hook.x + hook.w].any()
    assert not canvas[1800:, :80].any()


# Rendering


def test_runs_join_segments_closer_than_2_s():
    segs = [
        Segment(src_start=10.0, src_end=12.0, out_start=0.0),
        Segment(src_start=13.5, src_end=15.0, out_start=2.0),  # 1.5 s on: read through
        Segment(src_start=17.0, src_end=18.0, out_start=3.5),  # 2 s on: a seek
        Segment(src_start=5.0, src_end=6.0, out_start=4.5),  # back in the source
        Segment(src_start=6.5, src_end=7.6, out_start=5.5, speed=1.1),  # another speed
    ]
    assert runs(segs) == [segs[0:2], segs[2:3], segs[3:4], segs[4:5]]
    assert runs([]) == []


def test_render_video_short_edl(tmp_path):
    src = make_test_video(tmp_path / "src.mp4", seconds=4.0, size="1280x720", fps=30)
    style = Style()
    hook = hook_sprite("Watch this", None, style)
    span = Span(start=0.0, end=3.0, layout="face", boxes=PANELS["face"], crops=[[(438, 0, 404, 720)] * 90])
    layer = {"title": "Watch this", "accent_word": None, "start": 0.0, "end": 1.0, "scale": 1.0}
    edl = edl_for(chain([(0.5, 3.5, 1.0)]), [span], hook={**layer, "box": list(hook.box), "covers": None})
    out = tmp_path / "video.mp4"
    seen, before = [], threading.enumerate()
    render_video(edl, ClipSource(src), out, progress=seen.append)
    assert left_over(before) == []
    v = streams(out)["video"]
    assert (v["codec_name"], v["profile"], v["pix_fmt"], v["width"], v["height"]) == (
        "h264",
        "High",
        "yuv420p",
        1080,
        1920,
    )
    assert (v["color_range"], v["color_space"], v["color_transfer"], v["color_primaries"]) == (
        "tv",
        "bt709",
        "bt709",
        "bt709",
    )
    assert v["r_frame_rate"] == "30/1" and int(v["nb_frames"]) == 90
    assert keyframes(out) == pytest.approx([0.0, 2.0])  # a closed GOP every 2 s
    data = out.read_bytes()
    top = [kind for kind, _, _ in mp4_boxes(data)]
    assert top.index("moov") < top.index("mdat") and edit_lists(data) == 0
    assert seen and seen == sorted(seen) and seen[-1] == 1.0
    (first,) = decode_frames(ClipSource(out), 0.0, 1, fps=30.0, size=(W_OUT, H_OUT))
    x, y, w, h = hook.box
    white = (hook.rgba[..., 3] == 255) & (hook.rgba[..., :3].min(axis=2) == 255)
    assert first[y : y + h, x : x + w][white].mean() > 235  # the hook is drawn on frame 0


def test_render_video_takes_each_frame_from_its_source_time(tmp_path):
    src = video_from_frames(tmp_path / "count.mp4", (coded(k) for k in range(300)))
    segs = chain([(1.0, 2.0, 1.0), (2.5, 3.0, 1.0), (6.0, 7.0, 1.0), (7.5, 8.6, 1.1), (0.2, 0.5, 1.0)])
    n = Timeline(segs, 30.0).frames
    out = tmp_path / "video.mp4"
    render_video(edl_for(segs, [fit_span(n, (320, 180))], src=(320, 180)), ClipSource(src), out)
    got = [
        code_of(f[80:110, 20:88]) for f in decode_frames(ClipSource(out), 0.0, n, fps=30.0, size=(108, 192))
    ]
    want, speeds = [], []
    for s in segs:
        for k in range(Timeline([s], 30.0).frames):
            want.append(round((s.src_start + k * s.speed / 30.0) * 30.0))
            speeds.append(s.speed)
    assert len(got) == n
    assert [g for g, sp in zip(got, speeds, strict=True) if sp == 1.0] == [
        w for w, sp in zip(want, speeds, strict=True) if sp == 1.0
    ]
    assert all(abs(g - w) <= 1 for g, w, sp in zip(got, want, speeds, strict=True) if sp != 1.0)


def test_render_video_cuts_each_span_on_its_own_frames(tmp_path):
    src = video_from_frames(tmp_path / "wide.mp4", [gradient(640, 360)] * 60)
    left, right = (0, 0, 202, 360), (438, 0, 202, 360)
    spans = [
        Span(start=0.0, end=1.0, layout="face", boxes=PANELS["face"], crops=[[left] * 30]),
        Span(start=1.0, end=2.0, layout="face", boxes=PANELS["face"], crops=[[right] + [left] * 29]),
    ]
    out = tmp_path / "video.mp4"
    render_video(edl_for(chain([(0.0, 2.0, 1.0)]), spans, src=(640, 360)), ClipSource(src), out)
    blue = [f[:, :8, 0].mean() for f in decode_frames(ClipSource(out), 0.0, 60, fps=30.0, size=(108, 192))]
    assert blue[30] > 150  # the second span's first crop, from x 438 where blue is about 175
    assert all(b < 30 for k, b in enumerate(blue) if k != 30)


def test_render_video_puts_gameplay_under_the_speaker(tmp_path):
    talk_colour = (200, 60, 30)
    talk = video_from_frames(
        tmp_path / "talk.mp4", (np.full((360, 640, 3), talk_colour, np.uint8) for _ in range(75))
    )
    game = video_from_frames(tmp_path / "run.mp4", (coded(k) for k in range(30)))  # 1 s
    span = Span(
        start=0.0, end=2.0, layout="gameplay", boxes=PANELS["gameplay"], crops=[[(136, 0, 368, 360)] * 60]
    )
    edl = edl_for(
        chain([(0.0, 2.0, 1.0)]), [span], src=(640, 360), gameplay={"path": "run.mp4", "start_s": 0.5}
    )
    out = tmp_path / "video.mp4"
    render_video(edl, ClipSource(talk), out, gameplay=ClipSource(game))
    frames = list(decode_frames(ClipSource(out), 0.0, 60, fps=30.0, size=(108, 192)))
    assert len(frames) == 60
    for f in frames:
        assert np.abs(f[20:85, 20:88].reshape(-1, 3).mean(axis=0) - talk_colour).max() <= 8
    shown = [code_of(f[120:180, 20:88]) for f in frames]
    assert sum(abs(s - (15 + n) % 30) <= 1 for n, s in enumerate(shown)) >= 58  # from 0.5 s in, looping
    with pytest.raises(ToolError) as err:
        render_video(edl, ClipSource(talk), tmp_path / "bare.mp4")
    assert err.value.code == "gameplay_missing"
    assert not (tmp_path / "bare.mp4").exists()


def flash_and_click(path, fps):
    """When the bright frame shows and when the click starts, in the file's own times."""
    args = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time"]
    pts = [float(f["pts_time"]) for f in json.loads(run([*args, "-of", "json", str(path)]).stdout)["frames"]]
    raw = run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough"]
        + ["-vf", "scale=54:96", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    ).stdout
    light = np.frombuffer(raw, np.uint8).reshape(-1, 54 * 96).mean(axis=1)
    pcm = run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:a:0", "-af", "aresample=first_pts=0"]
        + ["-f", "f32le", "-ac", "1", "-"]
    ).stdout
    sound = np.abs(np.frombuffer(pcm, np.float32))
    return pts[int(np.argmax(light))], int(np.argmax(sound > 0.25 * sound.max())) / SR


@pytest.mark.parametrize("name", ["fast", "best"])
def test_render_59_94_source_keeps_audio_and_video_together(tmp_path, name):
    fps, flash, total = 59.94, 150, 240
    rate = 60000 / 1001
    pcm = np.zeros((round(total / rate * SR), 2), np.float32)
    at = round(flash / rate * SR)
    pcm[at : at + 1440] = (0.5 * np.sin(2 * np.pi * 1000 * np.arange(1440) / SR))[:, None]
    frames = (np.full((360, 640, 3), 255 if k == flash else 0, np.uint8) for k in range(total))
    src = video_from_frames(
        tmp_path / "src.mp4", frames, "60000/1001", audio=write_wav(tmp_path / "click.wav", pcm)
    )
    segs = Timeline.from_ranges([(0.5, 3.5)], fps).segments
    n = Timeline(segs, fps).frames
    edl = edl_for(segs, [fit_span(n, (640, 360), fps)], fps=fps, src=(640, 360), style=Style(encoder=name))
    video, sound, out = tmp_path / "video.mp4", tmp_path / "sound.m4a", tmp_path / "clip.mp4"
    render_video(edl, ClipSource(src), video)
    finish(assemble(decode_audio(ClipSource(src), 0.0, 4.0), 0.0, edl), SR, sound)
    mux(video, sound, out)
    s = streams(out)
    assert s["video"]["r_frame_rate"] == "60000/1001"
    assert abs(float(s["video"]["duration"]) - float(s["audio"]["duration"])) < 1 / fps
    seen, heard = flash_and_click(out, fps)
    assert -0.002 <= heard - seen < 1 / fps
    data = out.read_bytes()
    top = [kind for kind, _, _ in mp4_boxes(data)]
    assert top.index("moov") < top.index("mdat") and edit_lists(data) == 0


# The bitrate cap


def test_peak_rate_finds_the_busiest_second():
    packets = [(0.0, 1000), (0.5, 1000), (1.2, 5000), (1.9, 5000), (2.5, 100)]
    assert peak_rate(packets) == 8 * 10000
    assert peak_rate(packets, window_s=2.0) == 8 * 12000 / 2  # 0.0 to 2.0 holds the first four
    assert peak_rate([]) == 0.0
    assert peak_rate([(0.0, 1000), (1.0, 1000)]) == 8000  # a packet 1 s on starts the next second


def test_reencode_capped_brings_a_heavy_video_under_25_mbps(tmp_path):
    rng = np.random.default_rng(3)
    tone = write_wav(tmp_path / "tone.wav", 0.2 * np.sin(np.arange(round(1.5 * SR)) / 7.0)[:, None])
    noise = (rng.integers(0, 256, (960, 540, 3), dtype=np.uint8) for _ in range(45))
    heavy = video_from_frames(tmp_path / "heavy.mp4", noise, audio=tone, quality=("-qp", "0"))
    assert peak_bitrate(heavy) > 25_000_000
    assert reencode_capped(heavy) is True
    assert peak_bitrate(heavy) <= 25_000_000
    s = streams(heavy)
    assert (s["video"]["codec_name"], s["video"]["width"], s["video"]["height"]) == ("h264", 540, 960)
    assert "audio" in s and edit_lists(heavy.read_bytes()) == 0
    light = make_test_video(tmp_path / "light.mp4")
    before = light.read_bytes()
    assert reencode_capped(light) is False and light.read_bytes() == before


def test_render_video_stops_cleanly_when_told_to(tmp_path, started):
    src = make_test_video(tmp_path / "src.mp4", seconds=3.0)
    edl = edl_for(chain([(0.0, 3.0, 1.0)]), [fit_span(90, (640, 360))], src=(640, 360))
    calls = []

    def stop(fraction):
        calls.append(fraction)
        if len(calls) == 2:
            raise RuntimeError("stop")

    out, before = tmp_path / "video.mp4", threading.enumerate()
    with pytest.raises(RuntimeError, match="stop") as stopped:  # its traceback keeps the render's locals
        render_video(edl, ClipSource(src), out, progress=stop)
    assert not out.exists() and left_over(before) == []
    assert len(started) >= 2 and all(
        p.poll() is not None for p in started
    )  # the decoder and encoder are gone
    assert stopped.value.args == ("stop",)


def test_render_video_cleans_up_when_a_frame_fails(tmp_path, started, monkeypatch):
    src = make_test_video(tmp_path / "src.mp4", seconds=3.0)
    edl = edl_for(chain([(0.0, 3.0, 1.0)]), [fit_span(90, (640, 360))], src=(640, 360))
    real, calls = encode.compose, []

    def failing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 20:
            raise ValueError("bad frame")
        return real(*args, **kwargs)

    monkeypatch.setattr(encode, "compose", failing)
    out, before = tmp_path / "video.mp4", threading.enumerate()
    with pytest.raises(ValueError, match="bad frame"):
        render_video(edl, ClipSource(src), out)
    assert not out.exists() and left_over(before) == []
    assert len(started) >= 2 and all(p.poll() is not None for p in started)
