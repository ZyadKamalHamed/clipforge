"""Real YouTube. Run on purpose with: uv run pytest -m live tests/live/test_ytdlp_live.py -q"""

import json

import pytest

from clipforge.ingest.ytdlp import YtDlpClient, meta_from_info
from clipforge.media.ffmpeg import probe

pytestmark = pytest.mark.live

TED = "https://www.youtube.com/watch?v=arj7oStGLkU"
ZOO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", 19 seconds


def test_fetch_info_ted_has_heatmap():
    info = YtDlpClient().fetch_info(TED)
    meta = meta_from_info(info)
    assert meta.video_id == "arj7oStGLkU" and meta.duration_s > 800
    assert len(meta.heatmap) == 100
    assert meta.chapters and meta.caption_track == "en-orig"
    assert all(meta.video_bytes[q] for q in ("best", "balanced", "fast"))
    text = json.dumps(info)
    assert "http_headers" not in text and '"cookies"' not in text


def test_download_audio_and_captions_short_video(tmp_path):
    client = YtDlpClient()
    seen = []
    audio = client.download_audio(ZOO, tmp_path, progress=seen.append)
    info = probe(audio)
    assert info.has_audio and not info.has_video
    assert info.duration_s == pytest.approx(19, abs=1.5)
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)

    track = meta_from_info(client.fetch_info(ZOO)).caption_track
    assert track == "en"
    subs = client.download_captions(ZOO, tmp_path, track)
    assert subs is not None and subs.name == "subs.en.json3"
    assert "events" in json.loads(subs.read_text())


def test_download_sections_short_range(tmp_path):
    paths = YtDlpClient().download_sections(ZOO, tmp_path, [(2.0, 6.0), (10.0, 14.0)], "fast")
    assert len(paths) == 2 and paths[0] != paths[1]
    for path in paths:
        info = probe(path)
        assert info.has_video and info.has_audio
        assert info.duration_s == pytest.approx(4, abs=1.0)


def test_fetch_comments_live():
    comments = YtDlpClient().fetch_comments(ZOO, max_comments=20)
    assert 0 < len(comments) <= 20
    assert all(c.text for c in comments)
