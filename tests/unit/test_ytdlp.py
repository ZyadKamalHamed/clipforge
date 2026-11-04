import copy
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError

from clipforge.errors import ClipForgeError, NetworkError, SourceRefused, YouTubeRateLimited
from clipforge.ingest import section_child, ytdlp
from clipforge.ingest.ytdlp import (
    QUALITY,
    YtDlpClient,
    base_opts,
    classify_error,
    clean_info,
    estimate_video_bytes,
    js_runtimes,
    live_chat_command,
    meta_from_info,
    pick_caption_track,
    progress_hook,
    refusal_for,
    sections_command,
)
from clipforge.models import Comment, SourceMeta

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "info_min.json"
URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
AGE = "YouTube wants a sign-in for this one. Download it yourself and drop the file in."


def load_info() -> dict:
    return json.loads(FIXTURE.read_text())


def no_cookies(opts: dict) -> bool:
    return not any("cookie" in key.lower() for key in opts)


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: keeps the options it was made with and plays back a result."""

    def __init__(self, result=None, *, error=None, writes=()):
        self.result, self.error, self.writes = result or {}, error, writes
        self.opts, self.calls = None, []
        self.extractors, self.ie_key = [], None  # what the client added, and the one it asked for

    def __call__(self, opts):  # the client calls ydl_class(opts)
        self.opts = opts
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def add_info_extractor(self, ie):
        self.extractors.append(ie)

    def extract_info(self, url, download=True, ie_key=None):
        self.calls.append((url, download))
        self.ie_key = ie_key
        if self.error:
            raise DownloadError(self.error)
        home = Path(self.opts.get("paths", {}).get("home", "."))
        for name in self.writes:
            (home / name).write_bytes(b"{}")
        return copy.deepcopy(self.result)


# Metadata


def test_meta_from_info_fixture():
    meta = meta_from_info(load_info())
    assert isinstance(meta, SourceMeta)
    assert (meta.source_kind, meta.video_id) == ("youtube", "arj7oStGLkU")
    assert meta.title == "Inside the Mind of a Master Procrastinator | Tim Urban | TED"
    assert meta.duration_s == 844
    assert meta.channel == "TED"
    assert meta.channel_url == "https://www.youtube.com/channel/UCAuUUnT6oDeKwE6v1NGQxug"
    assert len(meta.heatmap) == 100 and len(meta.chapters) == 6
    assert meta.chapters[0].start == 0 and meta.chapters[0].title
    assert meta.is_creative_commons is False
    assert meta.caption_track == "en-orig"
    assert (meta.language, meta.live_status, meta.availability) == ("en", "not_live", "public")
    assert meta.video_bytes == {"best": 61673092, "balanced": 61673092, "fast": 67614947}


def test_meta_from_info_fallbacks():
    meta = meta_from_info(
        {
            "id": "abc",
            "title": "T",
            "duration": 61.5,
            "uploader": "Someone",
            "uploader_url": "https://www.youtube.com/@someone",
            "license": "Creative Commons Attribution license (reuse allowed)",
            "description": "x" * 6000,
            "heatmap": None,  # yt-dlp gives None when a video has no "Most replayed" graph
            "chapters": None,
        }
    )
    assert (meta.channel, meta.channel_url) == ("Someone", "https://www.youtube.com/@someone")
    assert meta.is_creative_commons is True
    assert len(meta.description) == 5000
    assert (meta.chapters, meta.heatmap, meta.age_limit) == ([], [], 0)
    assert meta.video_bytes == {"best": None, "balanced": None, "fast": None}
    assert meta.caption_track is None


def test_clean_info_drops_headers_cookies_and_requested_keys():
    raw = {
        "id": "abc",
        "title": "T",
        "http_headers": {"User-Agent": "x"},
        "cookies": "YSC=1",
        "formats": [
            {"format_id": "140", "url": "https://x", "http_headers": {"Cookie": "a=b"}, "cookies": "V=2"}
        ],
        "thumbnails": [{"url": "https://t", "http_headers": {"Referer": "y"}}],
        "requested_formats": [{"format_id": "140"}],
        "requested_subtitles": {"en": {}},
        "__post_extractor": lambda: {},
    }
    info = clean_info(raw)
    text = json.dumps(info)  # also proves it is plain JSON now
    assert "http_headers" not in text and "cookies" not in text and "requested_" not in text
    assert info["formats"][0]["format_id"] == "140" and info["title"] == "T"


# Refusals


NORMAL = {
    "source_kind": "youtube",
    "video_id": "abc",
    "title": "T",
    "channel": None,
    "channel_url": None,
    "duration_s": 600.0,
    "live_status": "not_live",
    "availability": "public",
}


@pytest.mark.parametrize(
    "changes, code, message",
    [
        (
            {"live_status": "is_live", "duration_s": 0},
            "live",
            "This stream is still live. Try again when it has ended.",
        ),
        (
            {"live_status": "post_live"},
            "processing",
            "This stream just ended and YouTube is still processing it. Try again in an hour.",
        ),
        ({"live_status": "is_upcoming", "duration_s": 0}, "upcoming", "This video hasn't premiered yet."),
        ({"availability": "private"}, "private", "This video is private."),
        ({"availability": "subscriber_only"}, "members", "This video is for channel members only."),
        ({"availability": "premium_only"}, "members", "This video is only for YouTube Premium members."),
        ({"availability": "needs_auth"}, "age", AGE),
        ({"age_limit": 18}, "age", AGE),
        (
            {"duration_s": 6 * 3600 + 1},
            "too_long",
            "This video is over 6 hours. Download the part you want and drop the file in.",
        ),
        (
            {"duration_s": 0},
            "unavailable",
            "YouTube didn't give this video's length, so ClipForge can't clip it. Try again later.",
        ),
    ],
)
def test_refusal_table(changes, code, message):
    refusal = refusal_for(SourceMeta(**{**NORMAL, **changes}))
    assert isinstance(refusal, SourceRefused)
    assert (refusal.code, str(refusal)) == (code, message)


def test_no_refusal_for_a_normal_video():
    assert refusal_for(SourceMeta(**NORMAL)) is None
    assert refusal_for(SourceMeta(**{**NORMAL, "duration_s": 6 * 3600})) is None  # exactly six hours is fine
    finished_stream = {"live_status": "was_live", "availability": "unlisted", "age_limit": 17}
    assert refusal_for(SourceMeta(**{**NORMAL, **finished_stream})) is None


def test_refusal_limit_follows_the_setting():
    refusal = refusal_for(SourceMeta(**{**NORMAL, "duration_s": 3 * 3600}), max_hours=2.5)
    assert refusal.code == "too_long" and str(refusal).startswith("This video is over 2.5 hours.")


def test_too_long_file_gets_advice_for_a_file():
    refusal = refusal_for(SourceMeta(**{**NORMAL, "source_kind": "file", "duration_s": 7 * 3600}))
    assert str(refusal) == "This video is over 6 hours. Trim it to the part you want and drop that in."


# Errors


@pytest.mark.parametrize(
    "message, kind, code",
    [
        (
            "ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser or "
            "--cookies for the authentication.",
            YouTubeRateLimited,
            "rate_limited",
        ),
        ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot.", YouTubeRateLimited, "rate_limited"),
        (
            "ERROR: unable to download video data: HTTP Error 429: Too Many Requests",
            YouTubeRateLimited,
            "rate_limited",
        ),
        (
            "ERROR: [youtube] abc: This content isn't available, try again later. "
            "The current session has been rate-limited by YouTube for up to an hour.",
            YouTubeRateLimited,
            "rate_limited",
        ),
        (
            "ERROR: [youtube] abc: Video unavailable. "
            "YouTube is requiring a captcha challenge before playback",
            YouTubeRateLimited,
            "rate_limited",
        ),
        (
            "ERROR: [youtube] abc: Sign in to confirm your age. "
            "This video may be inappropriate for some users.",
            SourceRefused,
            "age",
        ),
        ("ERROR: [youtube] abc: This video is age-restricted and needs a sign-in", SourceRefused, "age"),
        (
            "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video",
            SourceRefused,
            "private",
        ),
        ("ERROR: [youtube] abc: Video unavailable. This video is private.", SourceRefused, "private"),
        (
            "ERROR: [youtube] abc: Join this channel to get access to members-only content like this video",
            SourceRefused,
            "members",
        ),
        (
            "ERROR: [youtube] abc: Video unavailable. "
            "This video is available to this channel's members on level",
            SourceRefused,
            "members",
        ),
        ("ERROR: [youtube] abc: Premieres in 2 hours", SourceRefused, "upcoming"),
        ("ERROR: [youtube] abc: This live event will begin in a few moments.", SourceRefused, "upcoming"),
        (
            "ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader",
            SourceRefused,
            "unavailable",
        ),
        (
            "ERROR: [youtube] abc: The uploader has not made this video available in your country",
            SourceRefused,
            "unavailable",
        ),
        (
            "ERROR: [youtube] abc: Unable to download API page: The read operation timed out",
            NetworkError,
            "network",
        ),
        (
            "ERROR: unable to download video data: [Errno 54] Connection reset by peer",
            NetworkError,
            "network",
        ),
        ("ERROR: [youtube] abc: Temporary failure in name resolution", NetworkError, "network"),
        (
            "ERROR: [youtube] abc: Unable to download webpage: "
            "HTTPSConnection(host='www.youtube.com', port=443): "
            "Failed to resolve 'www.youtube.com' ([Errno 8] nodename nor servname provided, or not known)",
            NetworkError,
            "network",
        ),
        (
            "ERROR: [youtube] abc: Unable to download webpage: HTTP Error 503: Service Unavailable",
            NetworkError,
            "network",
        ),
        (
            "ERROR: ('Connection aborted.', "
            "RemoteDisconnected('Remote end closed connection without response'))",
            NetworkError,
            "network",
        ),
        ("ERROR: [youtube] abc: <urlopen error [Errno 51] Network is unreachable>", NetworkError, "network"),
    ],
)
def test_classify_error_table(message, kind, code):
    err = classify_error(message)
    assert type(err) is kind and err.code == code
    assert err.details == message
    assert "--cookies" not in str(err)  # the plain message never suggests handing over cookies


def test_classify_error_messages_are_plain_with_a_next_step():
    assert str(classify_error("HTTP Error 429")) == "YouTube is rate-limiting this Mac. Try again in an hour."
    assert str(classify_error("Private video")) == "This video is private."
    assert str(classify_error("Sign in to confirm your age")) == AGE
    network = classify_error("Read timed out")
    assert network.retryable and "Check the internet connection" in str(network)
    assert not classify_error("HTTP Error 429").retryable  # trying again at once only makes it worse


def test_unknown_errors_say_what_to_try():
    err = classify_error("ERROR: [youtube] abc: Something new and strange")
    assert type(err) is ClipForgeError and err.code == "ytdlp_failed"
    assert str(err).startswith("YouTube download failed.") and "updating yt-dlp" in str(err)
    assert err.details == "ERROR: [youtube] abc: Something new and strange"


# Options


def test_quality_formats():
    assert QUALITY == {
        "best": (
            "bv*[height<=2160][dynamic_range=SDR]+ba[ext=m4a]/bv*[height<=2160]+ba[ext=m4a]/b",
            ["lang", "res:2160", "vcodec:av01", "acodec:aac"],
        ),
        "balanced": (
            "bv*[height<=1440][dynamic_range=SDR]+ba[ext=m4a]/bv*[height<=1440]+ba[ext=m4a]/b",
            ["lang", "res:1440", "vcodec:av01", "acodec:aac"],
        ),
        "fast": (
            "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]",
            ["lang", "res:1080", "vcodec:h264", "acodec:aac"],
        ),
    }
    assert all(sort[0] == "lang" for _, sort in QUALITY.values())  # an auto-dubbed track is never picked


def stream(fid, height, vcodec, dynamic_range=None, ext="mp4"):
    return {
        "format_id": fid,
        "url": f"https://example.invalid/{fid}",
        "ext": ext,
        "protocol": "https",
        "vcodec": vcodec,
        "acodec": "none",
        "height": height,
        "width": height * 16 // 9,
        "fps": 30,
        "tbr": height,
        "dynamic_range": dynamic_range,
    }


AUDIO = {
    "format_id": "140",
    "url": "https://example.invalid/140",
    "ext": "m4a",
    "protocol": "https",
    "vcodec": "none",
    "acodec": "mp4a.40.2",
    "abr": 129,
}
HDR_AV1 = stream("701", 2160, "av01.0.13M.10.0.110.09.16.09.0", "HDR10")
HDR_VP9 = stream("337", 2160, "vp09.02.51.10.01.09.16.09.00", "HDR10", "webm")


def picked(quality, formats):
    """The format ids yt-dlp itself picks from ``formats`` for the preset, offline."""
    fmt, sort = QUALITY[quality]
    info = {"id": "abc", "title": "t", "extractor": "youtube", "extractor_key": "Youtube", "formats": formats}
    opts = {"format": fmt, "format_sort": list(sort), "quiet": True, "no_warnings": True, "simulate": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        out = ydl.process_ie_result(copy.deepcopy(info), download=False)
    return [f["format_id"] for f in out.get("requested_formats") or [out]]


def test_presets_take_sdr_over_hdr():
    """The renderer has no tone mapping, so an HDR stream is taken only when there is nothing else."""
    sdr = [stream("401", 2160, "av01.0.12M.08"), stream("313", 2160, "vp9", ext="webm")]
    sdr += [
        stream("400", 1440, "av01.0.12M.08"),
        stream("700", 1440, "av01.0.12M.10.0.110.09.16.09.0", "HDR10"),
    ]
    formats = [HDR_AV1, HDR_VP9, *sdr, AUDIO]
    assert picked("best", formats) == ["401", "140"]
    assert picked("balanced", formats) == ["400", "140"]
    assert picked("best", [HDR_AV1, AUDIO]) == ["701", "140"]  # HDR only: still downloads
    sized = [
        {**HDR_AV1, "filesize": 9_000},
        {**stream("401", 2160, "av01.0.12M.08"), "filesize": 7_000},
    ]
    assert estimate_video_bytes({"duration": 100, "formats": sized}, "best") == 7_000
    assert estimate_video_bytes({"duration": 100, "formats": sized[:1]}, "best") == 9_000


def fake_tool(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    tool = folder / name
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    return tool


def test_js_runtimes_prefers_venv_deno(tmp_path, monkeypatch):
    deno = fake_tool(tmp_path / "venv" / "bin", "deno")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "bin" / "python"))
    fake_tool(tmp_path / "path", "node")
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    assert js_runtimes() == {"deno": {"path": str(deno)}}


def test_js_runtimes_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "bin" / "python"))
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    node = fake_tool(tmp_path / "path", "node")
    assert js_runtimes() == {"node": {"path": str(node)}}
    deno = fake_tool(tmp_path / "path", "deno")
    assert js_runtimes() == {"deno": {"path": str(deno)}}  # deno on PATH beats node, as doctor checks


def test_js_runtimes_none_found(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert js_runtimes() == {}


def test_base_opts():
    opts = base_opts()
    assert no_cookies(opts)
    assert opts["quiet"] and opts["no_warnings"] and opts["noprogress"] and opts["noplaylist"] is True
    assert (opts["retries"], opts["fragment_retries"], opts["socket_timeout"]) == (3, 3, 30)
    assert opts["js_runtimes"] == js_runtimes()
    assert "player_skip" not in str(opts.get("extractor_args"))  # the heatmap needs the watch page
    sleeps = opts["retry_sleep_functions"]
    assert [sleeps["http"](n=n) for n in range(3)] == [1, 2, 4]  # retries back off
    assert sleeps["fragment"] is sleeps["http"] and sleeps["extractor"] is sleeps["http"]


def test_yt_dlp_output_goes_through_logging(caplog):
    logger = base_opts()["logger"]
    with caplog.at_level(logging.DEBUG, logger="clipforge.ytdlp"):
        logger.debug("[youtube] abc: Downloading webpage 100%")
        logger.info("[info] abc: Downloading 1 format(s)")
        logger.warning("WARNING: [youtube] abc: something odd")
        logger.error("ERROR: [youtube] abc: broke")
    got = [(r.name, r.levelname, r.getMessage()) for r in caplog.records]
    assert got == [
        ("clipforge.ytdlp", "DEBUG", "[youtube] abc: Downloading webpage 100%"),
        ("clipforge.ytdlp", "INFO", "[info] abc: Downloading 1 format(s)"),
        ("clipforge.ytdlp", "WARNING", "WARNING: [youtube] abc: something odd"),
        ("clipforge.ytdlp", "ERROR", "ERROR: [youtube] abc: broke"),
    ]


# Sizes and caption tracks


def test_estimate_video_bytes():
    info = load_info()
    # yt-dlp picks 399-sr (AV1) over the bigger VP9 248-sr at the same height, so the estimate does too.
    assert estimate_video_bytes(info, "best") == 61673092
    assert estimate_video_bytes(info, "balanced") == 61673092
    # Fast takes H.264 only: the DASH 720p with a known size, not the HLS copy that has only a peak bitrate.
    assert estimate_video_bytes(info, "fast") == 67614947


def video_format(fid, height, vcodec="vp9", **extra):
    return {"format_id": fid, "height": height, "vcodec": vcodec, "acodec": "none", **extra}


def test_estimate_video_bytes_fallbacks():
    only_bitrate = {"duration": 100, "formats": [video_format("a", 720, "avc1.4d401f", tbr=800)]}
    assert estimate_video_bytes(only_bitrate, "fast") == 10_000_000  # 800 kbit/s for 100 s
    assert estimate_video_bytes({**only_bitrate, "duration": None}, "fast") is None
    approx = {"duration": 100, "formats": [video_format("a", 720, tbr=800, filesize_approx=12345)]}
    assert estimate_video_bytes(approx, "best") == 12345
    assert estimate_video_bytes(approx, "fast") is None  # no H.264 stream at all
    tall = {
        "duration": 100,
        "formats": [
            video_format("4k", 2160, filesize=9_000),
            video_format("1440", 1440, filesize=4_000),
            video_format("storyboard", 90, "none"),
            video_format("unknown", None, filesize=1),
        ],
    }
    assert estimate_video_bytes(tall, "best") == 9_000
    assert estimate_video_bytes(tall, "balanced") == 4_000
    assert estimate_video_bytes({"duration": 100, "formats": []}, "best") is None


@pytest.mark.parametrize(
    "info, track",
    [
        ({"language": "en", "automatic_captions": {"en": [], "en-orig": [], "fr": []}}, "en-orig"),
        ({"language": "es", "automatic_captions": {"en-orig": [], "es-orig": []}}, "es-orig"),
        ({"language": None, "automatic_captions": {"fr-orig": [], "en": []}}, "fr-orig"),
        ({"language": "de", "subtitles": {"de": []}, "automatic_captions": {"en": []}}, "de"),
        # "Me at the zoo": no language, no -orig track, a manual and an automatic "en"
        (
            {"language": None, "subtitles": {"de": [], "en": []}, "automatic_captions": {"en": [], "fr": []}},
            "en",
        ),
        ({"automatic_captions": {"en-orig": []}}, "en-orig"),
        ({"automatic_captions": {"fr": []}, "subtitles": {"live_chat": []}}, None),
        ({}, None),
        # a dubbed stream has an original track for each dub, and the video's language may have a region
        ({"language": "en-US", "automatic_captions": {"ar-orig": [], "en-orig": []}}, "en-orig"),
        ({"language": "es", "automatic_captions": {"en-orig": [], "es-US-orig": []}}, "es-US-orig"),
        ({"language": "pt-BR", "subtitles": {"pt": []}, "automatic_captions": {"en": []}}, "pt"),
        # nothing in the video's language: never another language's track, nor a translation of one
        ({"language": "en-US", "automatic_captions": {"ar-orig": [], "ru-orig": [], "en": []}}, None),
        ({"language": "es", "automatic_captions": {"en": [], "fr": []}}, None),
        ({"language": None, "automatic_captions": {"ar-orig": [], "en-orig": []}}, None),
    ],
)
def test_pick_caption_track_variants(info, track):
    assert pick_caption_track(info) == track


# The client, with a stand-in for yt-dlp


def test_fetch_info_returns_clean_info():
    fake = FakeYDL({"id": "abc", "title": "T", "http_headers": {"User-Agent": "x"}, "requested_formats": []})
    info = YtDlpClient(ydl_class=fake).fetch_info(URL)
    assert fake.calls == [(URL, False)] and no_cookies(fake.opts)
    assert "http_headers" not in info and "requested_formats" not in info and info["id"] == "abc"


CHANNEL = "UCabcdefghijklmnopqrstuv"
CHANNEL_URL = "https://www.youtube.com/@apodcast/videos"


def flat_listing(**changes):
    entries = [
        {"_type": "url", "id": "bbbbbbbbbb2", "title": "Episode 2"},
        {"_type": "url", "id": "not an id", "title": "Dropped"},
        {"_type": "url", "id": "aaaaaaaaaa1", "title": "Episode\x00 1"},
    ]
    listing = {
        "_type": "playlist",
        "id": CHANNEL,
        "channel_id": CHANNEL,
        "channel": "A Podcast",
        "title": "A Podcast - Videos",
        "entries": entries,
    }
    return listing | changes


def test_list_channel_options():
    fake = FakeYDL(flat_listing())
    listing = YtDlpClient(ydl_class=fake).list_channel(CHANNEL_URL, 2)
    assert fake.calls == [(CHANNEL_URL, False)] and no_cookies(fake.opts)
    assert fake.opts["extract_flat"] == "in_playlist" and fake.opts["playlistend"] == 2
    assert fake.opts["noplaylist"] is False
    assert (listing.channel_id, listing.title) == (CHANNEL, "A Podcast")
    assert listing.url == f"https://www.youtube.com/channel/{CHANNEL}/videos"
    assert listing.entries == [("bbbbbbbbbb2", "Episode 2"), ("aaaaaaaaaa1", "Episode  1")]


def test_list_channel_without_a_channel_id_is_refused():
    fake = FakeYDL(flat_listing(id="PL123", channel_id=None))
    with pytest.raises(ClipForgeError) as err:
        YtDlpClient(ydl_class=fake).list_channel(CHANNEL_URL, 1)
    assert err.value.message == ytdlp.NO_CHANNEL


def test_list_channel_keeps_to_the_limit():
    many = [{"id": f"{n:011d}", "title": str(n)} for n in range(8)]
    listing = YtDlpClient(ydl_class=FakeYDL(flat_listing(entries=many))).list_channel(CHANNEL_URL, 3)
    assert [vid for vid, _ in listing.entries] == ["00000000000", "00000000001", "00000000002"]


def test_list_channel_rate_limit_is_classified():
    fake = FakeYDL(error="ERROR: Sign in to confirm you're not a bot")
    with pytest.raises(YouTubeRateLimited):
        YtDlpClient(ydl_class=fake).list_channel(CHANNEL_URL, 1)


@pytest.mark.parametrize(
    "said",
    [
        "ERROR: [youtube:tab] @gone: YouTube said: This account has been terminated for a violation of "
        "YouTube's Terms of Service.",
        "ERROR: [youtube:tab] UCx: YouTube said: This channel was removed because it violated our "
        "Community Guidelines.",
        "ERROR: [youtube:tab] @gone: YouTube said: This channel does not exist.",
        "ERROR: [youtube:tab] @gone: The channel/playlist does not exist and the URL redirected to "
        "youtube.com home page",
        "ERROR: [youtube:tab] @gone: Unable to download webpage: HTTP Error 404: Not Found",
    ],
)
def test_list_channel_says_when_the_channel_is_gone(said):
    with pytest.raises(SourceRefused) as err:
        YtDlpClient(ydl_class=FakeYDL(error=said)).list_channel(CHANNEL_URL, 1)
    assert (err.value.code, err.value.message) == ("channel_gone", ytdlp.CHANNEL_GONE)
    assert err.value.details == said
    assert ytdlp.CHANNEL_GONE == "YouTube says this channel was removed or doesn't exist. Check its link."


def test_list_channel_keeps_other_failures_as_they_were():
    with pytest.raises(ClipForgeError) as err:
        YtDlpClient(
            ydl_class=FakeYDL(error="ERROR: [youtube:tab] @x: This channel does not have a Shorts tab")
        ).list_channel(CHANNEL_URL, 1)
    assert (err.value.code, err.value.message) == ("ytdlp_failed", ytdlp.FAILED)


def test_download_audio_asks_for_the_original_audio(tmp_path):
    out = tmp_path / "audio.m4a"
    fake = FakeYDL({"requested_downloads": [{"filepath": str(out)}]})
    seen = []
    assert YtDlpClient(ydl_class=fake).download_audio(URL, tmp_path, progress=seen.append) == out
    opts = fake.opts
    assert opts["format"] == "ba[ext=m4a]/ba/b" and opts["format_sort"] == ["lang"]
    assert opts["outtmpl"] == {"default": "audio.%(ext)s"} and opts["paths"] == {"home": str(tmp_path)}
    assert len(opts["progress_hooks"]) == 1 and no_cookies(opts)
    assert fake.calls == [(URL, True)]


@pytest.mark.parametrize("quality", ["best", "balanced", "fast"])
def test_download_video_uses_the_quality_preset(tmp_path, quality):
    out = tmp_path / "video.mp4"
    fake = FakeYDL({"requested_downloads": [{"filepath": str(out)}]})
    assert YtDlpClient(ydl_class=fake).download_video(URL, tmp_path, quality) == out
    opts = fake.opts
    assert (opts["format"], opts["format_sort"]) == QUALITY[quality]
    assert opts["merge_output_format"] == "mp4" and opts["outtmpl"] == {"default": "video.%(ext)s"}
    assert not opts.get("progress_hooks") and no_cookies(opts)


def test_sections_here_names_each_range(tmp_path):
    files = [tmp_path / "video.2.0-6.0.mp4", tmp_path / "video.10.0-14.0.mp4"]
    fake = FakeYDL({"requested_downloads": [{"filepath": str(f)} for f in files]})
    got = YtDlpClient(ydl_class=fake).sections_here(URL, tmp_path, [(2.0, 6.0), (10.0, 14.0)], "fast")
    assert got == files
    opts = fake.opts
    assert opts["force_keyframes_at_cuts"] is True and opts["merge_output_format"] == "mp4"
    ranges = list(opts["download_ranges"]({"duration": 19}, None))
    assert ranges == [{"start_time": 2.0, "end_time": 6.0}, {"start_time": 10.0, "end_time": 14.0}]
    template = opts["outtmpl"]["default"]
    assert "%(section_start)" in template and "%(section_end)" in template  # two ranges never share a name
    assert (opts["format"], opts["format_sort"]) == QUALITY["fast"] and no_cookies(opts)


def test_sections_are_cut_with_a_quick_encode(tmp_path):
    # Exact cuts mean ffmpeg encodes the section again. On 4K AV1, libx264's veryfast at quality 18 took
    # half the time of its default and kept more of the picture; the sound keeps more than the default too.
    fake = FakeYDL({"requested_downloads": [{"filepath": str(tmp_path / "video.2.0-6.0.mp4")}]})
    YtDlpClient(ydl_class=fake).sections_here(URL, tmp_path, [(2.0, 6.0)], "best")
    assert fake.opts["external_downloader_args"] == {
        "ffmpeg_o": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]
    }


def test_download_sections_with_no_ranges_downloads_nothing(tmp_path):
    fake = FakeYDL()
    # yt-dlp reads an empty range list as "the whole video"
    assert YtDlpClient(ydl_class=fake).sections_here(URL, tmp_path, [], "best") == []
    assert YtDlpClient(ydl_class=fake).download_sections(URL, tmp_path, [], "best") == []
    assert fake.calls == [] and list(tmp_path.iterdir()) == []


def test_download_captions_options(tmp_path):
    fake = FakeYDL(writes=["subs.en-orig.json3"])
    got = YtDlpClient(ydl_class=fake).download_captions(URL, tmp_path, "en-orig")
    assert got == tmp_path / "subs.en-orig.json3"
    opts = fake.opts
    assert (
        opts["skip_download"] is True and opts["writeautomaticsub"] is True and opts["writesubtitles"] is True
    )
    assert (opts["subtitleslangs"], opts["subtitlesformat"]) == (["en-orig"], "json3")
    assert opts["sleep_interval_subtitles"] == 5
    assert opts["outtmpl"] == {"default": "subs.%(ext)s"} and no_cookies(opts)


def test_download_captions_missing_track_gives_none(tmp_path):
    assert YtDlpClient(ydl_class=FakeYDL()).download_captions(URL, tmp_path, "en-orig") is None


def test_fetch_comments_keeps_text_and_likes():
    comments = [
        {"id": "1", "text": "3:12 is the best part", "like_count": 41, "author": "a", "parent": "root"},
        {"id": "2", "text": "agreed", "like_count": None, "parent": "1"},  # a reply
        {"id": "3", "text": "", "like_count": 2, "parent": "root"},
        {"id": "4", "text": "lol", "parent": "root"},
    ]
    fake = FakeYDL({"comments": comments})
    got = YtDlpClient(ydl_class=fake).fetch_comments(URL, max_comments=50)
    assert got == [Comment(text="3:12 is the best part", likes=41), Comment(text="lol", likes=0)]
    assert fake.opts["getcomments"] is True
    assert fake.opts["extractor_args"] == {
        "youtube": {"comment_sort": ["top"], "max_comments": ["50", "50", "0"]}
    }
    assert fake.calls == [(URL, False)]
    assert YtDlpClient(ydl_class=FakeYDL({"comments": None})).fetch_comments(URL) == []


def test_fetch_comments_stops_at_the_cap():
    comments = [{"text": f"c{i}", "like_count": i, "parent": "root"} for i in range(10)]
    got = YtDlpClient(ydl_class=FakeYDL({"comments": comments})).fetch_comments(URL, max_comments=3)
    assert [c.text for c in got] == ["c0", "c1", "c2"]


CALLS = {
    "fetch_info": lambda c, d: c.fetch_info(URL),
    "download_audio": lambda c, d: c.download_audio(URL, d),
    "download_captions": lambda c, d: c.download_captions(URL, d, "en"),
    "fetch_comments": lambda c, d: c.fetch_comments(URL),
    "download_video": lambda c, d: c.download_video(URL, d, "best"),
    "sections_here": lambda c, d: c.sections_here(URL, d, [(1.0, 2.0)], "best"),
}


@pytest.mark.parametrize("call", sorted(CALLS))
def test_download_errors_become_plain_errors(tmp_path, call):
    error = "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video"
    with pytest.raises(SourceRefused) as caught:
        CALLS[call](YtDlpClient(ydl_class=FakeYDL(error=error)), tmp_path)
    assert caught.value.code == "private" and caught.value.details == error


def test_progress_only_rises_in_steps_of_at_least_one_percent():
    seen = []
    hook = progress_hook(seen.append)
    for done in (0, 4, 300, 500, 505, 1000):
        hook({"status": "downloading", "downloaded_bytes": done, "total_bytes": 1000})
    hook({"status": "finished", "downloaded_bytes": 1000, "total_bytes": 1000})
    # then the audio file starts from zero
    hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
    hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes_estimate": 100})
    hook({"status": "downloading", "downloaded_bytes": 50})  # size unknown
    hook({"status": "finished"})
    assert seen == [0.3, 0.5, 1.0]


def test_progress_reports_the_end_even_after_a_small_last_step():
    seen = []
    hook = progress_hook(seen.append)
    hook({"status": "downloading", "downloaded_bytes": 995, "total_bytes": 1000})
    hook({"status": "finished"})
    assert seen == [0.995, 1.0]


# Live chat replay, in a child process


def test_live_chat_command_never_reads_config_or_cookies(tmp_path):
    cmd = live_chat_command(URL, tmp_path)
    assert cmd[:3] == [sys.executable, "-m", "yt_dlp"]
    assert "--ignore-config" in cmd and not any("cookie" in part for part in cmd)
    assert "--skip-download" in cmd and "--write-subs" in cmd and "--no-playlist" in cmd
    assert cmd[cmd.index("--sub-langs") + 1] == "live_chat"
    assert cmd[cmd.index("-P") + 1] == str(tmp_path) and cmd[cmd.index("-o") + 1] == "chat"
    assert cmd[-1] == URL
    for name, config in js_runtimes().items():
        assert cmd[cmd.index("--js-runtimes") + 1] == f"{name}:{config['path']}"


def fake_chat(monkeypatch, script: str) -> None:
    monkeypatch.setattr(
        ytdlp, "live_chat_command", lambda url, dest_dir: [sys.executable, "-c", script, str(dest_dir)]
    )


def test_live_chat_returns_the_chat_file(tmp_path, monkeypatch):
    fake_chat(
        monkeypatch, "import sys, pathlib; pathlib.Path(sys.argv[1], 'chat.live_chat.json').write_text('{}')"
    )
    assert YtDlpClient().download_live_chat(URL, tmp_path) == tmp_path / "chat.live_chat.json"


def test_live_chat_without_a_replay_gives_none(tmp_path, monkeypatch):
    fake_chat(monkeypatch, "pass")
    assert YtDlpClient().download_live_chat(URL, tmp_path) is None


def test_live_chat_failure_is_classified(tmp_path, monkeypatch):
    fake_chat(
        monkeypatch,
        "import sys; print('ERROR: HTTP Error 429: Too Many Requests', file=sys.stderr); sys.exit(1)",
    )
    with pytest.raises(YouTubeRateLimited) as caught:
        YtDlpClient().download_live_chat(URL, tmp_path)
    assert "HTTP Error 429" in caught.value.details


def test_live_chat_is_stopped_at_the_timeout(tmp_path, monkeypatch):
    script = (
        "import sys, pathlib, time\n"
        "pathlib.Path(sys.argv[1], 'chat.live_chat.json.part').write_text('{}')\n"
        "time.sleep(30)"
    )
    fake_chat(monkeypatch, script)
    started = time.monotonic()
    with pytest.raises(ClipForgeError) as caught:
        YtDlpClient().download_live_chat(URL, tmp_path, timeout_s=1.0)
    assert time.monotonic() - started < 10
    assert caught.value.code == "chat_timeout" and "took too long" in str(caught.value)
    assert not list(tmp_path.glob("chat.*"))  # the partial file is gone


# Sections, in a child process


def test_sections_command_runs_the_child_isolated(tmp_path):
    cmd = sections_command(URL, tmp_path, [(2.0, 6.0), (10.5, 14.0)], "fast")
    assert cmd[:5] == [sys.executable, "-I", "-m", "clipforge.ingest.section_child", str(os.getpid())]
    assert cmd[5:] == [URL, str(tmp_path), "fast", "2.0", "6.0", "10.5", "14.0"]
    assert not any("cookie" in part for part in cmd)


def fake_sections(monkeypatch, script: str) -> None:
    monkeypatch.setattr(
        ytdlp,
        "sections_command",
        lambda url, dest_dir, ranges, quality: [sys.executable, "-c", script, str(dest_dir)],
    )


def gone(pid: int, within_s: float = 5.0) -> bool:
    """Whether process ``pid`` has ended within ``within_s``."""
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_download_sections_moves_what_the_child_wrote(tmp_path, monkeypatch):
    script = (
        "import json, sys, pathlib\n"
        "out = pathlib.Path(sys.argv[1], 'video.2.0-6.0.mp4')\n"
        "out.write_bytes(b'section')\n"
        "print('a line before the reply')\n"
        "print(json.dumps({'paths': [str(out)]}))"
    )
    fake_sections(monkeypatch, script)
    got = YtDlpClient().download_sections(URL, tmp_path, [(2.0, 6.0)], "fast")
    assert got == [tmp_path / "video.2.0-6.0.mp4"] and got[0].read_bytes() == b"section"
    assert [p.name for p in tmp_path.iterdir()] == ["video.2.0-6.0.mp4"]  # the attempt's own folder is gone


def test_download_sections_failure_is_classified(tmp_path, monkeypatch):
    reply = "{'error': 'ERROR: HTTP Error 429: Too Many Requests'}"
    fake_sections(monkeypatch, f"import json; print(json.dumps({reply})); raise SystemExit(2)")
    with pytest.raises(YouTubeRateLimited) as caught:
        YtDlpClient().download_sections(URL, tmp_path, [(2.0, 6.0)], "fast")
    assert caught.value.details == "ERROR: HTTP Error 429: Too Many Requests"
    fake_sections(monkeypatch, "raise SystemExit('the child broke')")  # and no reply at all
    with pytest.raises(ClipForgeError) as caught:
        YtDlpClient().download_sections(URL, tmp_path, [(2.0, 6.0)], "fast")
    assert (caught.value.code, caught.value.details) == ("ytdlp_failed", "the child broke")
    assert list(tmp_path.iterdir()) == []


def test_download_sections_is_stopped_at_its_time_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp, "SECTIONS_BASE_S", 0.5)
    monkeypatch.setattr(ytdlp, "SECTIONS_PER_S", 0.0)
    script = (
        "import sys, pathlib, time\n"
        "pathlib.Path(sys.argv[1], 'video.2.0-6.0.mp4.part').write_bytes(b'half')\n"
        "time.sleep(30)"
    )
    fake_sections(monkeypatch, script)
    started = time.monotonic()
    with pytest.raises(ClipForgeError) as caught:
        YtDlpClient().download_sections(URL, tmp_path, [(2.0, 6.0)], "fast")
    assert time.monotonic() - started < 10
    assert caught.value.code == "sections_timeout" and "took too long" in str(caught.value)
    assert not caught.value.retryable and list(tmp_path.iterdir()) == []  # the partial file is gone


def test_download_sections_stops_with_its_children_when_the_job_is_cancelled(tmp_path, monkeypatch):
    pid_file, out = tmp_path / "ffmpeg.pid", tmp_path / "sections"
    out.mkdir()
    script = (
        "import pathlib, subprocess, sys, time\n"
        "ffmpeg = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"done = pathlib.Path({str(pid_file)!r})\n"
        "done.with_suffix('.tmp').write_text(str(ffmpeg.pid))\n"
        "done.with_suffix('.tmp').rename(done)\n"
        "time.sleep(30)"
    )
    fake_sections(monkeypatch, script)
    started = time.monotonic()
    with pytest.raises(ClipForgeError) as caught:
        YtDlpClient().download_sections(URL, out, [(2.0, 6.0)], "fast", cancelled=pid_file.exists)
    assert time.monotonic() - started < 10 and caught.value.code == "cancelled"
    assert gone(int(pid_file.read_text()))  # what the child started, as ffmpeg, is stopped with it
    assert list(out.iterdir()) == []


def test_the_real_section_child_hands_back_yt_dlps_error(tmp_path):
    # yt-dlp refuses a file:// address without touching the network.
    with pytest.raises(ClipForgeError) as caught:
        YtDlpClient().download_sections("file:///nowhere/video.mp4", tmp_path, [(0.0, 1.0)], "fast")
    assert caught.value.code == "ytdlp_failed" and "file:// URLs are disabled" in caught.value.details
    assert list(tmp_path.iterdir()) == []


def test_the_section_child_prints_the_files_or_the_error(tmp_path, monkeypatch, capsys):
    files = [tmp_path / "video.2.0-6.0.mp4", tmp_path / "video.10.5-14.0.mp4"]
    asked = []

    class Client:
        def sections_here(self, url, dest_dir, ranges, quality):
            asked.append((url, dest_dir, ranges, quality))
            return files

    monkeypatch.setattr(section_child, "YtDlpClient", Client)
    assert section_child.main([URL, str(tmp_path), "fast", "2.0", "6.0", "10.5", "14.0"]) == 0
    assert asked == [(URL, tmp_path, [(2.0, 6.0), (10.5, 14.0)], "fast")]
    assert json.loads(capsys.readouterr().out) == {"paths": [str(f) for f in files]}

    class Refusing:
        def sections_here(self, url, dest_dir, ranges, quality):
            raise classify_error("ERROR: [youtube] abc: Private video")

    monkeypatch.setattr(section_child, "YtDlpClient", Refusing)
    assert section_child.main([URL, str(tmp_path), "fast", "2.0", "6.0"]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "ERROR: [youtube] abc: Private video"}


def test_the_section_child_stops_itself_once_clipforge_is_gone():
    # ClipForge here is a process that starts the child and quits at once.
    child = (
        "import sys, threading, time\n"
        "from clipforge.ingest.section_child import watch\n"
        "threading.Thread(target=watch, args=(int(sys.argv[1]),), daemon=True).start()\n"
        "time.sleep(30)"
    )
    clipforge = (
        "import os, subprocess, sys\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-I', '-c', sys.argv[1], str(os.getpid())],\n"
        "    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,\n"
        ")\n"
        "print(child.pid)"
    )
    run = subprocess.run([sys.executable, "-I", "-c", clipforge, child], capture_output=True, text=True)
    assert gone(int(run.stdout))
