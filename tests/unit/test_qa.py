"""What QA checks on a finished clip before it can be posted (SPEC 7.11, 8.1, 8.4, 10.2, 10.4), and the
cover picture the clip card and Instagram show (SPEC 7.10 step 5)."""

from __future__ import annotations

import dataclasses
import re

import cv2
import numpy as np
import pytest

from clipforge.errors import ToolError
from clipforge.media.ffmpeg import ffmpeg_bin, ffprobe_bin, run
from clipforge.render import cover as cov
from clipforge.render.audio import AudioReport, finish
from clipforge.render.captions import CaptionPage, PageWord, paginate
from clipforge.render.cover import THUMB_W, cover_frame, make_cover
from clipforge.render.edl import EDL, GAMEPLAY_BOX, NO_CREDIT, NO_HOOK, SHAPING, EdlWord, Span, Zoom, fit_box
from clipforge.render.encode import COLOUR, TO_709
from clipforge.render.qa import QAResult, judge, measure, mp4_problem, run_qa, upscale_of
from clipforge.render.styles import Style
from clipforge.render.timeline import Timeline
from tests.helpers import make_wav

SECONDS, FPS = 3.2, 30.0
FRAMES = round(SECONDS * FPS)
GOOD_AUDIO = AudioReport(input_lufs=-21.0, output_lufs=-14.2, true_peak=-1.6, silent=False)
HOOK_BOX = [190, 270, 700, 190]  # a 700 px wide hook title, centred at the top of the safe zone
CREDIT_BOX = [380, 472, 320, 44]
FACE = (380, 560, 320, 400)  # inside the safe zone, under the hook title and the credit line
CAPTION_BOX = (330, 1094, 420, 112)
WORDS = [("So", 0.2, 0.45), ("this", 0.5, 0.75), ("is", 0.8, 0.95), ("...", 1.0, 1.15), ("huge.", 1.2, 1.6)]
MP4 = ("-movflags", "+faststart", "-use_editlist", "0")
LUMA_STEP = 255 / 219  # one step of video luma (16 to 235) in the 0 to 255 of a JPEG
DASHES = chr(0x2013) + chr(0x2014)


def one_sentence(text):
    """One plain sentence: a capital first, one full stop at the end, no dashes."""
    return (
        re.fullmatch(r"[A-Z0-9][^\n]*[.]", text) is not None
        and re.search(r"[.!?] [A-Z]", text) is None
        and not any(c in text for c in DASHES)
    )


def render_like(path, *, size="1080x1920", seconds=SECONDS, black=False, audio=True, hush=False, mp4=MP4):
    """A file made the way ClipForge renders: H.264 yuv420p at 30 fps and AAC stereo at 48 kHz, its index
    first and no edit list; ``black`` fades it in from black and ``hush`` makes its sound digital silence."""
    args = [ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={seconds}"]
    if audio:
        tone = "anullsrc=r=48000:cl=stereo" if hush else "sine=frequency=440:sample_rate=48000"
        args += ["-f", "lavfi", "-i", f"{tone}:duration={seconds}"]
        args += ["-c:a", "aac", "-ac", "2", "-ar", "48000"]
    if black:
        args += ["-vf", "fade=t=in:st=0:d=0.5"]
    run(
        [*args, "-c:v", "libx264", "-preset", "ultrafast", "-bf", "0", "-pix_fmt", "yuv420p", *mp4, str(path)]
    )
    return path


def ramp(path, seconds=2.0):
    """A grey picture of luma 16 + 3k at frame k, stored losslessly, with sound muxed as ClipForge muxes it
    (so its first frame lasts 1024 samples longer than the rest)."""
    grey = f"nullsrc=s=16x16:r=30:d={seconds},geq=lum='16+3*N':cb=128:cr=128,scale=1080:1920:flags=neighbor"
    run(
        [ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"{grey},format=yuv420p",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-c:a", "aac", "-ac", "2", *MP4, str(path)]
    )  # fmt: skip
    return path


def frame_starts(path):
    """When each frame of ``path`` shows, in the file's own timing."""
    args = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time"]
    return sorted(float(t) for t in run([*args, "-of", "csv=p=0", str(path)]).stdout.split())


@pytest.fixture(scope="module")
def good(tmp_path_factory):
    return render_like(tmp_path_factory.mktemp("qa") / "clip.mp4")


@pytest.fixture(scope="module")
def measured(good):
    return measure(good)


def edl_words(words=WORDS):
    return [EdlWord(i=i, text=t, start=a, end=b) for i, (t, a, b) in enumerate(words)]


def placed(pages, box=CAPTION_BOX):
    for page in pages:
        page.box = tuple(box)
    return pages


def face_span(faces=None, *, n=FRAMES, crop=(438, 0, 404, 720)):
    """The face layout all clip long, cut from a 1280 x 720 source, with the faces seen every 0.1 s."""
    if faces is None:
        faces = [(k / 10, *FACE) for k in range(round(n / FPS * 10))]
    return Span(
        start=0.0, end=n / FPS, layout="face", boxes=[(0, 0, 1080, 1920)], crops=[[crop] * n], faces=faces
    )


def good_edl(**changes):
    segments = Timeline.from_ranges([(10.0, 10.0 + SECONDS)], FPS).segments
    words = edl_words()
    fire = {"emoji": chr(0x1F525), "word": "huge.", "start": 1.2, "end": 2.0, "box": [500, 980, 96, 96]}
    fields = {
        "clip_id": "c1",
        "fps": FPS,
        "src_width": 1280,
        "src_height": 720,
        "duration": Timeline(segments, FPS).duration,
        "segments": segments,
        "words": words,
        "pages": placed(paginate(words)),
        "hook": {
            "title": "This is huge",
            "accent_word": "huge",
            "start": 0.0,
            "end": SECONDS,
            "scale": 1.0,
            "box": list(HOOK_BOX),
            "covers": None,
        },
        "credit": {"text": "Source: A Channel", "start": 0.0, "end": SECONDS, "box": list(CREDIT_BOX)},
        "emoji": [fire | {"size": 96}],
        "spans": [face_span()],
        "style": Style(),
    }
    fields.update(changes)
    return EDL(**fields)


def check(result, name):
    (found,) = [c for c in result.checks if c.name == name]
    return found


def judged(measured, edl=None, *, audio=GOOD_AUDIO, upscale=1.0, **info):
    """QA's verdict on the good render, with ``info`` in place of what probing it found."""
    if info:
        measured = dataclasses.replace(measured, info=dataclasses.replace(measured.info, **info))
    return judge(measured, edl or good_edl(), audio, upscale=upscale)


def verdict(found):
    """A check's message when it failed, None when it passed."""
    return None if found.ok else found.message


# The file


def test_qa_passes_a_good_render(good):
    result = run_qa(good, good_edl(), GOOD_AUDIO, upscale=1.0)
    assert [c.name for c in result.checks if not c.ok] == []
    assert result.passed and result.problems() == []
    assert result.passed_for("instagram") and result.passed_for("tiktok")
    assert [c.name for c in result.checks] == [
        "codec", "size", "fps_range", "fps", "duration", "instagram_length", "tiktok_length", "file_size",
        "bitrate", "mp4", "audio", "audio_format", "first_frame", "loudness", "safe_zone", "hook_face",
        "captions", "upscale", "hdr", "originality",
    ]  # fmt: skip
    assert {c.name for c in result.checks if c.hard} == {
        "codec", "size", "fps_range", "instagram_length", "tiktok_length", "file_size", "bitrate", "mp4",
        "audio", "audio_format", "first_frame",
    }  # fmt: skip
    assert {c.name: c.platform for c in result.checks if c.platform} == {
        "instagram_length": "instagram",
        "tiktok_length": "tiktok",
        "file_size": "instagram",
        "bitrate": "instagram",
        "mp4": "instagram",
        "audio_format": "instagram",
    }
    assert all(c.message == "" for c in result.checks)
    assert QAResult.model_validate_json(result.model_dump_json()) == result
    assert result.model_dump()["passed"] is True


def test_qa_wrong_size_is_a_hard_failure(tmp_path, measured):
    small = render_like(tmp_path / "small.mp4", size="720x1280")
    result = run_qa(small, good_edl(), GOOD_AUDIO, upscale=1.0)
    size = check(result, "size")
    assert (size.ok, size.hard, size.platform) == (False, True, None) and not result.passed
    assert size.message == "The video is 720 x 1280 instead of 1080 x 1920."
    assert result.problems() == [size.message]
    assert not result.passed_for("instagram") and not result.passed_for("tiktok")
    for change, message in [
        ({"vcodec": "hevc"}, "The video uses HEVC, where the apps need H.264."),
        ({"pix_fmt": "yuv444p"}, "The video's colour format is yuv444p, where the apps need yuv420p."),
        ({"has_video": False, "vcodec": None, "pix_fmt": None}, "The file has no picture."),
    ]:
        codec = check(judged(measured, **change), "codec")
        assert (codec.ok, codec.hard, codec.message) == (False, True, message)


def test_qa_black_first_frame(tmp_path, measured):
    dark = render_like(tmp_path / "dark.mp4", black=True)
    result = run_qa(dark, good_edl(), GOOD_AUDIO, upscale=1.0)
    first = check(result, "first_frame")
    assert (first.ok, first.hard, first.platform) == (False, True, None) and not result.passed
    assert first.message == "The first frame is black, so the clip would open on nothing."
    assert measure(dark).first_luma == pytest.approx(16, abs=1) and measured.first_luma > 40
    for luma, ok in [(20.0, False), (20.5, True), (None, True)]:  # black is a mean luma of 20 or under
        assert check(judged(dataclasses.replace(measured, first_luma=luma)), "first_frame").ok is ok


def test_qa_missing_audio_is_a_hard_failure(tmp_path):
    mute = render_like(tmp_path / "mute.mp4", audio=False)
    result = run_qa(mute, good_edl(), GOOD_AUDIO, upscale=1.0)
    sound = check(result, "audio")
    assert (sound.ok, sound.hard, sound.platform) == (False, True, None) and not result.passed
    assert sound.message == "The video has no sound track."
    assert check(result, "audio_format").ok and check(result, "duration").ok


def test_qa_fps_and_duration_must_match_the_edl(measured):
    def off(name, edl=None, **info):
        found = check(judged(measured, edl, **info), name)
        assert not found.hard
        return verdict(found)

    assert off("fps") is None and off("duration") is None
    assert off("fps", good_edl(fps=25.0)) == "The video runs at 30 fps instead of 25 fps."
    assert off("fps", good_edl(fps=30000 / 1001)) == "The video runs at 30 fps instead of 29.97 fps."
    assert off("duration", frames=FRAMES + 1) is None  # within a frame
    assert off("duration", frames=FRAMES - 1) is None
    assert off("duration", frames=FRAMES + 2) == "The picture lasts 3.27 s instead of 3.20 s."
    # with no frame count, the stream's length, which an MP4's longer first frame stretches like the sound's
    assert off("duration", frames=0, video_duration_s=3.25) is None
    assert off("duration", frames=0, video_duration_s=3.27) == "The picture lasts 3.27 s instead of 3.20 s."
    assert off("duration", frames=0, video_duration_s=0.0) is None  # the file doesn't say
    assert off("duration", audio_duration_s=0.0) is None
    # the sound may run on by a frame plus the 1024 samples the AAC encoder leads in with
    assert off("duration", audio_duration_s=3.2 + 1 / 30 + 1024 / 48000) is None
    assert off("duration", audio_duration_s=3.2 + 1 / 30 + 1024 / 48000 + 0.002) == (
        "The sound lasts 3.26 s instead of 3.20 s."
    )
    assert off("duration", audio_duration_s=3.14) == "The sound lasts 3.14 s instead of 3.20 s."
    assert off("duration", frames=FRAMES + 2, audio_duration_s=3.3) == (
        "The picture lasts 3.27 s and the sound 3.30 s, instead of 3.20 s."
    )


def test_qa_platform_limits(measured):
    def length(name, seconds):
        found = check(judged(measured, duration_s=seconds), name)
        assert found.hard
        return verdict(found)

    assert length("instagram_length", 3.0) is None and length("instagram_length", 900.0) is None
    assert length("instagram_length", 2.95) == "The clip is 2.9 s long, under Instagram's 3 s minimum."
    assert (
        length("instagram_length", 900.4) == "The clip is 15 min 1 s long, over Instagram's 15 minute limit."
    )
    assert length("tiktok_length", 600.0) is None and length("tiktok_length", 2.0) is None
    assert length("tiktok_length", 600.2) == "The clip is 10 min 1 s long, over TikTok's 10 minute limit."
    assert length("tiktok_length", 960.0) == "The clip is 16 min long, over TikTok's 10 minute limit."
    long = judged(measured, duration_s=700.0)
    assert not long.passed and long.passed_for("instagram") and not long.passed_for("tiktok")
    short = judged(measured, duration_s=2.0)
    assert short.passed_for("tiktok") and not short.passed_for("instagram")

    assert check(judged(measured, size_bytes=300_000_000), "file_size").ok
    big = check(judged(measured, size_bytes=312_400_000), "file_size")
    assert (big.ok, big.hard) == (False, True)
    assert big.message == "The file is 313 MB, over Instagram's 300 MB limit."

    def rate(bps):
        return verdict(check(judged(dataclasses.replace(measured, peak_bps=bps)), "bitrate"))

    assert rate(25_000_000) is None
    assert 0 < measured.peak_bps < 25_000_000  # the good render's own, read from the file
    assert rate(27_050_000) == "The busiest second of the video is 27.1 Mbps, over Instagram's 25 Mbps limit."

    for fps, ok in [(23.0, True), (60.0, True), (22.98, False), (60.5, False)]:
        found = check(judged(measured, nominal_fps=fps, fps=fps), "fps_range")
        assert found.ok is ok and found.hard and found.platform is None
    slow = check(judged(measured, nominal_fps=22.98, fps=22.98), "fps_range")
    assert slow.message == "The video runs at 22.98 fps, outside the 23 to 60 fps the apps take."
    assert check(judged(measured, nominal_fps=0.0, fps=22.98), "fps_range").message == slow.message
    worst = judged(measured, duration_s=900.4, size_bytes=312_400_000, nominal_fps=61.0)
    assert len(worst.problems()) >= 4 and all(one_sentence(p) for p in worst.problems())
    hard = [c.message for c in worst.checks if not c.ok and c.hard]
    soft = [c.message for c in worst.checks if not c.ok and not c.hard]
    assert soft and worst.problems() == hard + soft  # the ones that block posting first


def test_qa_audio_format(measured):
    def sound(**info):
        found = check(judged(measured, **info), "audio_format")
        assert found.hard and found.platform == "instagram"
        return verdict(found)

    assert sound() is None and sound(sample_rate=44100, channels=1) is None
    assert sound(acodec="mp3") == "The sound uses MP3, where Instagram needs AAC."
    assert sound(sample_rate=96000) == "The sound is sampled at 96 kHz, over Instagram's 48 kHz limit."
    assert sound(sample_rate=88200) == "The sound is sampled at 88.2 kHz, over Instagram's 48 kHz limit."
    assert sound(channels=6) == "The sound has 6 channels, where Instagram takes 1 or 2."
    assert sound(has_audio=False, acodec=None, sample_rate=0, channels=0) is None  # "audio" says so


def test_qa_mp4_layout(tmp_path, good):
    assert mp4_problem(good) is None
    late = render_like(tmp_path / "late.mp4", mp4=("-use_editlist", "0"))
    assert mp4_problem(late) == "The file's index comes after its media, where Instagram needs it first."
    edits = render_like(tmp_path / "edits.mp4", mp4=("-movflags", "+faststart"))
    assert mp4_problem(edits) == "The file has an edit list, which Instagram rejects."
    movie = render_like(tmp_path / "movie.mov")
    assert mp4_problem(movie) == "The file is a QuickTime movie, not an MP4."
    other = render_like(tmp_path / "other.mkv", mp4=())
    assert mp4_problem(other) == "The file is not an MP4."
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(good.read_bytes()[:40])
    assert mp4_problem(cut) == "The file is not an MP4."
    result = run_qa(late, good_edl(), GOOD_AUDIO, upscale=1.0)
    layout = check(result, "mp4")
    assert (layout.ok, layout.hard, layout.platform) == (False, True, "instagram")
    assert layout.message == mp4_problem(late)
    assert result.passed_for("tiktok") and not result.passed_for("instagram") and not result.passed


def box(kind, body=b""):
    return (8 + len(body)).to_bytes(4, "big") + kind + body


def test_mp4_problem_reads_the_boxes(tmp_path):
    ftyp = box(b"ftyp", b"isom" + bytes(4))
    moov = box(b"moov", box(b"trak", box(b"tkhd", bytes(8))))
    edits = box(b"moov", box(b"trak", box(b"edts")))
    mdat = (1).to_bytes(4, "big") + b"mdat" + (16 + 8).to_bytes(8, "big") + bytes(8)  # a 64-bit size
    late = "The file's index comes after its media, where Instagram needs it first."
    for data, problem in [
        (ftyp + mdat + moov, late),
        (ftyp + edits + mdat, "The file has an edit list, which Instagram rejects."),
        (ftyp + moov + mdat, None),
        (moov + mdat, "The file is not an MP4."),  # no file type first
    ]:
        (tmp_path / "f.mp4").write_bytes(data)
        assert mp4_problem(tmp_path / "f.mp4") == problem


# The edit list


def test_qa_loudness_off_target_is_a_soft_warning(measured):
    def loud(lufs, silent=False):
        report = GOOD_AUDIO.model_copy(update={"output_lufs": lufs, "silent": silent})
        found = check(judged(measured, audio=report), "loudness")
        assert not found.hard
        return verdict(found)

    assert loud(-14.0) is None and loud(-15.5) is None and loud(-12.5) is None
    assert loud(-15.6) == "The sound is -15.6 LUFS instead of -14, so it will play quieter than other clips."
    assert loud(-12.4) == "The sound is -12.4 LUFS instead of -14, so it will play louder than other clips."
    assert loud(-99.0, silent=True) == "The clip is nearly silent, so its sound was left as it was."


def test_qa_says_when_a_clip_is_nearly_silent(tmp_path):
    report = finish(np.zeros((round(SECONDS * 48000), 2), np.float32), 48000, tmp_path / "sound.m4a")
    result = run_qa(render_like(tmp_path / "hush.mp4", hush=True), good_edl(), report, upscale=1.0)
    loudness = check(result, "loudness")
    assert report.silent and (loudness.ok, loudness.hard) == (False, False)
    assert loudness.message == "The clip is nearly silent, so its sound was left as it was."
    assert result.passed and result.problems() == [loudness.message]


def test_qa_text_outside_the_safe_zone(measured):
    def zone(**changes):
        found = check(judged(measured, good_edl(**changes)), "safe_zone")
        assert not found.hard
        return verdict(found)

    hook, credit, fire = good_edl().hook, good_edl().credit, good_edl().emoji[0]
    card = {"text": "A fact", "start": 1.0, "end": 2.0, "box": [40, 600, 400, 120]}
    reach = "reaches outside the safe zone, where the apps' buttons can cover it."
    assert zone() is None
    assert zone(hook=hook | {"box": [90, 270, 850, 190]}) is None  # touching its edges is inside
    assert zone(pages=placed(paginate(edl_words()), (330, 1150, 420, 100))) is None  # down to y 1250
    assert zone(hook=hook | {"box": [91, 270, 850, 190]}) == f"The hook title {reach}"
    assert zone(hook=hook | {"box": [89, 270, 700, 190]}) == f"The hook title {reach}"
    assert zone(credit=credit | {"box": [380, 269, 320, 44]}) == f"The credit line {reach}"
    pages = placed(paginate(edl_words()))
    pages[1].box = (330, 1151, 420, 100)
    assert zone(pages=pages) == f"A caption {reach}"
    pages[1].box = None  # not placed, so not drawn
    assert zone(pages=pages) is None
    assert zone(cards=[card]) == f"A fact card {reach}"
    assert zone(emoji=[fire | {"box": [900, 980, 96, 96]}]) == f"An emoji {reach}"
    low = placed(paginate(edl_words()), (330, 1151, 420, 100))
    assert zone(hook=hook | {"box": [91, 270, 850, 190]}, pages=low, cards=[card, card]) == (
        "The hook title, 2 captions and 2 fact cards reach outside the safe zone, where the apps' buttons "
        "can cover them."
    )
    assert zone(emoji=[fire | {"box": [900, 980, 96, 96]}] * 2) == (
        "2 emoji reach outside the safe zone, where the apps' buttons can cover them."
    )
    assert zone(hook=None, credit=None, pages=[], emoji=[]) is None
    assert judged(measured, good_edl(cards=[card])).passed  # a warning, never a block


def test_qa_hook_over_a_face(measured):
    over = [(k / 10, 380, 300, 320, 400) for k in range(10)]  # under the hook title from 0 to 1 s
    rest = [(k / 10, *FACE) for k in range(10, 32)]

    def covered(faces, **changes):
        found = check(judged(measured, good_edl(spans=[face_span(faces)], **changes)), "hook_face")
        assert not found.hard
        return verdict(found)

    assert covered(None) is None
    assert covered(over + rest) == "The hook title covers a face for 1.0 s."
    two = over + [(t, 600, 300, 200, 200) for t, *_ in over]  # two faces at once count once
    assert covered(two + rest) == "The hook title covers a face for 1.0 s."
    assert (
        covered(over + rest, hook=good_edl().hook | {"end": 0.5}) == "The hook title covers a face for 0.5 s."
    )
    assert covered(over + rest, hook=None) is None
    assert covered([(k / 10, 380, 460, 320, 400) for k in range(32)]) is None  # just under its bottom edge
    every_2 = [(k * 0.08, 380, 300, 320, 400) for k in range(10)]  # at 25 fps a face is seen every 2 frames
    assert covered(every_2, fps=25.0) == "The hook title covers a face for 0.8 s."
    assert judged(measured, good_edl(spans=[face_span(over + rest)])).passed


def test_qa_lets_the_hook_cover_a_forehead(measured):
    def covered(faces):
        return verdict(check(judged(measured, good_edl(spans=[face_span(faces)])), "hook_face"))

    # the hook title (y 270 to 460) over only the top fifth of a face is not covering it
    assert covered([(k / 10, 380, 400, 320, 400) for k in range(32)]) is None  # the fifth ends at y 480
    low = [(k / 10, *FACE) for k in range(10, 32)]
    eyes = [(k / 10, 380, 370, 320, 400) for k in range(10)]  # the fifth ends at y 450
    assert covered(eyes + low) == "The hook title covers a face for 1.0 s."


def test_qa_caption_words_must_match(measured):
    def captions(pages, **changes):
        found = check(judged(measured, good_edl(pages=pages, **changes)), "captions")
        assert not found.hard
        return verdict(found)

    def shown_as(*groups):
        """Caption pages showing these groups of (word index, text)."""
        pages = []
        for group in groups:
            words = [
                PageWord(i=i, text=t, start=1.0 if i is None else WORDS[i][1], end=1.6) for i, t in group
            ]
            pages.append(CaptionPage(start=words[0].start, end=1.6, words=words, box=CAPTION_BOX))
        return pages

    said = [(0, "So"), (1, "this"), (2, "is")]
    assert captions(placed(paginate(edl_words()))) is None
    assert captions(shown_as(said, [(4, "huge.")])) is None  # "..." has nothing to show
    assert captions(shown_as(said, [(4, "huge")])) is None  # nor do the stops at a word's edges
    assert (
        captions(shown_as([(0, "So"), (2, "is")], [(4, "huge.")]))
        == 'The captions leave out "this" at 0.5 s.'
    )
    assert captions(shown_as(said)) == 'The captions leave out "huge" at 1.2 s.'
    assert captions(shown_as([(0, "So")], [(4, "huge.")])) == (
        'The captions leave out 2 words, the first "this" at 0.5 s.'
    )
    assert captions(shown_as([(0, "So"), (1, "that"), (2, "is")], [(4, "huge.")])) == (
        'The captions show "that" where the clip says "this".'
    )
    assert captions(shown_as(said, [(4, "huge."), (None, "wow")])) == (
        'The captions show "wow", which the clip never says.'
    )
    assert captions(shown_as(said, [(4, "huge."), (3, "...")])) == (
        'The captions show "...", which the clip never says.'
    )
    assert captions(shown_as(said, [(2, "is"), (4, "huge.")])) == 'The captions show "is" twice.'
    assert captions(shown_as([(1, "this"), (0, "So"), (2, "is")], [(4, "huge.")])) == (
        "The captions show the words out of order."
    )
    assert captions([]) == "The clip has no captions."
    assert captions([], notes=[SHAPING]) == (
        "Captions are off for this clip because its words use a script ClipForge cannot draw yet, like "
        "Arabic, Hebrew or Hindi."
    )
    assert captions([], words=edl_words([("...", 0.2, 0.5)])) is None  # nothing to caption


def test_qa_upscale_is_a_soft_warning(measured):
    def up(factor):
        return check(judged(measured, upscale=factor), "upscale")

    assert up(1.0).ok and up(1.9).ok
    soft = up(2.4)
    assert (soft.ok, soft.hard) == (False, False)
    assert soft.message == (
        "The picture is enlarged 2.4 times, so it may look soft; a higher quality download would be sharper."
    )
    assert not up(1.95).ok
    assert judged(measured, upscale=2.4).passed


def test_qa_hdr_source_is_a_soft_warning(measured):
    plain = check(judge(measured, good_edl(), GOOD_AUDIO, upscale=1.0), "hdr")
    hdr = check(judge(measured, good_edl(), GOOD_AUDIO, upscale=1.0, hdr=True), "hdr")
    assert plain.ok and (hdr.ok, hdr.hard) == (False, False)
    assert hdr.message == (
        "The source video is HDR, so ClipForge converted its colours and its brightest parts may look"
        " clipped."
    )


def test_qa_originality(measured):
    def light(own=False, **changes):
        found = check(judge(measured, good_edl(**changes), GOOD_AUDIO, upscale=1.0, own=own), "originality")
        assert not found.hard
        return verdict(found)

    card = {"text": "A fact", "start": 1.0, "end": 2.0, "box": [190, 600, 400, 120]}
    assert light() is None  # a hook title and the credit line
    assert light(credit=None, cards=[card]) is None
    assert (
        light(credit=None) == "Light on original edits: add a fact card or the credit line to the hook title."
    )
    assert light(hook=None) == (
        "Light on original edits: the clip needs a hook title and a fact card or credit line."
    )
    assert light(credit=None, own=True) is None  # their own video needs nothing more
    assert light(hook=None, notes=[NO_HOOK]) == (
        "Light on original edits: the hook title is off for this clip because ClipForge cannot draw its "
        "letters yet."
    )
    assert light(credit=None, notes=[NO_CREDIT]) == (
        "Light on original edits: the on-screen credit is off for this clip because ClipForge cannot draw "
        "the channel's name yet, so add a fact card."
    )


def test_cold_open_counts_as_original(measured):
    """A cold open is an edit of ClipForge's own, like a fact card or the credit line (SPEC 8.4)."""
    cold = {"sid": "S2", "src_start": 12.0, "src_end": 14.6, "out_end": 2.6}
    found = check(
        judge(measured, good_edl(credit=None, cold_open=cold), GOOD_AUDIO, upscale=1.0), "originality"
    )
    assert verdict(found) is None
    found = check(
        judge(measured, good_edl(hook=None, cold_open=cold), GOOD_AUDIO, upscale=1.0), "originality"
    )
    assert (
        verdict(found)
        == "Light on original edits: the clip needs a hook title and a fact card or credit line."
    )


def test_voiceover_counts_as_original(measured):
    """A voiceover is your own words over the clip, an edit as original as a fact card (SPEC 8.4)."""
    take = {"path": "/x/voiceover/0123456789.wav", "at": 1.0, "duration_s": 2.0}
    found = check(
        judge(measured, good_edl(credit=None, voiceover=take), GOOD_AUDIO, upscale=1.0), "originality"
    )
    assert verdict(found) is None
    found = check(judge(measured, good_edl(credit=None), GOOD_AUDIO, upscale=1.0), "originality")
    assert verdict(found) == "Light on original edits: add a fact card or the credit line to the hook title."


def test_qa_says_what_is_off_for_a_script_clipforge_cannot_draw(measured):
    edl = good_edl(pages=[], hook=None, credit=None, notes=[SHAPING, NO_HOOK, NO_CREDIT])
    result = judged(measured, edl)
    assert result.passed and result.problems() == [
        "Captions are off for this clip because its words use a script ClipForge cannot draw yet, like "
        "Arabic, Hebrew or Hindi.",
        "Light on original edits: the hook title is off for this clip because ClipForge cannot draw its "
        "letters yet.",
    ]


def test_upscale_of_leaves_out_the_punch_ins_extra_zoom():
    punch = face_span()
    punch.crops[0][30:45] = [(474, 27, 374, 666)] * 15  # 1.08 times closer from 1 s to 1.5 s
    edl = good_edl(spans=[punch], zooms=[Zoom(start=1.0, end=1.5, scale=1.08)])
    assert upscale_of(edl) == pytest.approx(1080 / 404, rel=0.01)


def test_upscale_of_takes_the_most_enlarged_panel():
    fit = Span(start=0.0, end=1.0, layout="fit", boxes=[fit_box(1280, 720)], crops=[[(0, 0, 1280, 720)] * 30])
    assert upscale_of(good_edl(spans=[fit])) == pytest.approx(1080 / 1280)
    assert upscale_of(good_edl(spans=[fit.model_copy(update={"crops": []})])) == pytest.approx(1080 / 1280)
    assert upscale_of(good_edl(spans=[])) == pytest.approx(1080 / 1280)  # drawn as the fit layout
    assert upscale_of(good_edl(spans=[face_span().model_copy(update={"crops": []})])) == 1.0  # nothing cut
    assert upscale_of(good_edl(spans=[fit, face_span()])) == pytest.approx(1080 / 404)
    zoom = face_span()
    zoom.crops[0][40] = (500, 100, 303, 540)  # a punch in
    assert upscale_of(good_edl(spans=[zoom])) == pytest.approx(1080 / 303)
    wide = face_span(crop=(0, 0, 1400, 800))  # more than the frame, so cut to it as it's drawn
    assert upscale_of(good_edl(spans=[wide])) == pytest.approx(1920 / 720)
    stacked = Span(
        start=0.0,
        end=1.0,
        layout="stacked",
        boxes=[(0, 0, 1080, 960), (0, 960, 1080, 960)],
        crops=[[(0, 0, 640, 569)] * 30, [(700, 100, 480, 427)] * 30],
    )
    assert upscale_of(good_edl(spans=[stacked])) == pytest.approx(1080 / 480)
    gameplay = Span(
        start=0.0,
        end=1.0,
        layout="gameplay",
        boxes=[(0, 0, 1080, 1056), GAMEPLAY_BOX],
        crops=[[(100, 0, 675, 660)] * 30],
    )
    assert upscale_of(good_edl(spans=[gameplay])) == pytest.approx(1.6)  # the gameplay video aside


# The cover


def test_cover_and_thumb_written(tmp_path):
    video = ramp(tmp_path / "clip.mp4")
    faces = [
        (0.2, 400, 600, 200, 240),  # small
        (0.6, 340, 520, 400, 480),  # big, inside the safe zone, clear of the hook title: the cover
        (0.7, 20, 600, 500, 600),  # bigger, but past the safe zone's left edge
        (0.8, 300, 300, 480, 560),  # bigger, but under the hook title
        (1.2, 290, 500, 500, 600),  # biggest, in the second half
    ]
    segments = Timeline.from_ranges([(10.0, 12.0)], FPS).segments
    hook = good_edl().hook | {"end": 2.0}
    edl = good_edl(segments=segments, duration=2.0, spans=[face_span(faces, n=60)], hook=hook)
    cover, thumb = tmp_path / "cover.jpg", tmp_path / "thumb.jpg"
    at = make_cover(video, edl, cover, thumb)
    big, small = cv2.imread(str(cover)), cv2.imread(str(thumb))
    assert big.shape == (1920, 1080, 3) and small.shape == (640, THUMB_W, 3)
    assert big.mean() == pytest.approx(3 * 18 * LUMA_STEP, abs=1.5)  # frame 18, at 0.6 s
    assert small.mean() == pytest.approx(3 * 18 * LUMA_STEP, abs=1.5)
    starts = frame_starts(video)
    assert at == pytest.approx((starts[18] + starts[19]) / 2)  # in the middle of when the file shows it
    assert starts[18] != pytest.approx(18 / 30)  # which is not 18 / 30: the file's first frame runs long
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip.mp4", "cover.jpg", "thumb.jpg"]
    with pytest.raises(ToolError):
        make_cover(video, edl, tmp_path / "gone" / "cover.jpg", thumb)


def test_cover_of_a_file_shorter_than_its_edit_list(tmp_path):
    video = ramp(tmp_path / "clip.mp4")
    edl = good_edl(duration=6.4, spans=[face_span([(3.0, 340, 520, 400, 480)], n=192)], hook=None)
    at = make_cover(video, edl, tmp_path / "cover.jpg", tmp_path / "thumb.jpg")
    assert cv2.imread(str(tmp_path / "cover.jpg")).mean() == pytest.approx(3 * 59 * LUMA_STEP, abs=1.5)
    starts = frame_starts(video)
    assert starts[59] < at < starts[59] + 1 / 30  # its last frame


def test_cover_of_a_frame_cut_short(tmp_path, monkeypatch):
    video = ramp(tmp_path / "clip.mp4")
    real = cov.run

    def short(args, **kwargs):
        done = real(args, **kwargs)
        if args[0] == ffmpeg_bin():
            done.stdout = done.stdout[:-1]
        return done

    monkeypatch.setattr(cov, "run", short)
    with pytest.raises(ToolError):
        make_cover(video, good_edl(), tmp_path / "cover.jpg", tmp_path / "thumb.jpg")
    assert not (tmp_path / "cover.jpg").exists()


def test_cover_needs_a_picture(tmp_path):
    sound = make_wav(tmp_path / "a.wav", [(1.0, 0.5)])
    with pytest.raises(ToolError):
        make_cover(sound, good_edl(), tmp_path / "cover.jpg", tmp_path / "thumb.jpg")
    assert not (tmp_path / "cover.jpg").exists()


def test_cover_keeps_the_colours(tmp_path):
    """The cover shows the colours a player shows: a render's BT.709 read as BT.709."""
    video = tmp_path / "red.mp4"
    run(
        [ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xC03020:s=1080x1920:r=30:d=0.5",
         "-vf", TO_709, "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", *COLOUR, *MP4, str(video)]
    )  # fmt: skip
    make_cover(video, good_edl(spans=[]), tmp_path / "cover.jpg", tmp_path / "thumb.jpg")
    for name in ("cover.jpg", "thumb.jpg"):
        b, g, r = cv2.imread(str(tmp_path / name)).reshape(-1, 3).mean(axis=0)
        assert (r, g, b) == pytest.approx((192, 48, 32), abs=3)  # read as BT.601, red and green are 13 off


def test_cover_frame_keeps_to_the_hook_title_while_it_shows():
    faces = [(0.5, 400, 600, 200, 240), (0.9, 340, 520, 400, 480)]
    assert cover_frame(good_edl(spans=[face_span(faces)])) == 27  # the bigger face, at 0.9 s
    fading = good_edl().hook | {"end": 1.0}  # fully shown until 0.8 s, then fading out
    assert cover_frame(good_edl(spans=[face_span(faces)], hook=fading)) == 15
    assert cover_frame(good_edl(spans=[face_span([(0.8, 340, 520, 400, 480)])], hook=fading)) == 24
    assert cover_frame(good_edl(spans=[face_span(faces)], hook=None)) == 27  # no hook: the first half
    halves = [(1.5, 400, 600, 200, 240), (1.6, 340, 520, 400, 480)]  # the clip is 3.2 s
    assert cover_frame(good_edl(spans=[face_span(halves)], hook=None)) == 45
    tie = [(0.3, 340, 520, 400, 480), (0.9, 340, 520, 400, 480)]
    assert cover_frame(good_edl(spans=[face_span(tie)])) == 9  # the earlier of two the same size
    beside = [(0.3, 340, 520, 400, 480), (0.3, 100, 1000, 100, 100), (0.6, 400, 600, 200, 240)]
    assert cover_frame(good_edl(spans=[face_span(beside)])) == 9  # the largest face at a time counts
    first, second = face_span([(0.5, 400, 600, 200, 240)], n=30), face_span([(1.2, 340, 520, 400, 480)])
    assert cover_frame(good_edl(spans=[first, second.model_copy(update={"start": 1.0})])) == 36  # every span
    edges = [(0.5, 90, 270, 850, 980)]  # touching the safe zone's four edges is inside it
    assert cover_frame(good_edl(spans=[face_span(edges)], hook=None)) == 15


def test_cover_frame_may_show_the_hook_over_a_forehead():
    brow = [(k / 10, 380, 400, 320, 400) for k in range(1, 16)]  # the hook title ends in its top fifth
    assert cover_frame(good_edl(spans=[face_span(brow)])) == 3


def test_cover_frame_falls_back_to_the_first_frame():
    assert cover_frame(good_edl(spans=[face_span([])])) == 0
    outside = [(k / 10, 20, 600, 500, 600) for k in range(1, 16)]
    assert cover_frame(good_edl(spans=[face_span(outside)])) == 0
    low = [(k / 10, 340, 800, 400, 480) for k in range(1, 16)]  # past y 1250
    assert cover_frame(good_edl(spans=[face_span(low)])) == 0
    covered = [(k / 10, 380, 300, 320, 400) for k in range(1, 16)]  # all under the hook title
    assert cover_frame(good_edl(spans=[face_span(covered)])) == 0
    assert cover_frame(good_edl(spans=[])) == 0
    right = [(k / 10, 600, 600, 400, 480) for k in range(1, 16)]  # past x 940
    assert cover_frame(good_edl(spans=[face_span(right)])) == 0
    high = [(k / 10, 340, 200, 400, 480) for k in range(1, 16)]  # above y 270
    assert cover_frame(good_edl(spans=[face_span(high)], hook=None)) == 0
