import json
import math
from pathlib import Path

import pytest

from clipforge.ingest.youtube_signals import (
    CapEvent,
    CapWord,
    Hotspot,
    chat_bursts,
    comment_hotspots,
    comment_timestamps,
    parse_json3,
    parse_live_chat,
)
from clipforge.models import Comment

FIXTURES = Path(__file__).parent.parent / "fixtures"
SAMPLE = FIXTURES / "sample.json3"


def one_event(text: str, start_ms: int = 1000, duration_ms: int = 2000) -> dict:
    return {"events": [{"tStartMs": start_ms, "dDurationMs": duration_ms, "segs": [{"utf8": text}]}]}


# Captions


def test_parse_json3_words_and_times():
    words, _ = parse_json3(SAMPLE)
    assert [w.text for w in words] == ["so", "this", "works", "really", "well", "Thank", "you", "all"]
    assert [w.start for w in words] == pytest.approx([1.0, 1.4, 1.9, 2.5, 3.1, 7.5, 8.0, 8.5])
    # Each word ends where the next begins, but never after its own event: "well" stops at 5.5.
    assert [w.end for w in words] == pytest.approx([1.4, 1.9, 2.5, 3.1, 5.5, 8.0, 8.5, 9.0])


def test_parse_json3_takes_the_parsed_dict_too():
    assert parse_json3(json.loads(SAMPLE.read_text())) == parse_json3(SAMPLE)


def test_parse_json3_skips_append_newlines_and_empty_events():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 1000, "id": 1},  # a window definition, no segs
            {"tStartMs": 0, "dDurationMs": 1000, "aAppend": 1, "segs": [{"utf8": "ghost"}]},
            {
                "tStartMs": 100,
                "dDurationMs": 900,
                "segs": [{"utf8": "\n"}, {"utf8": ""}, {"utf8": " real", "tOffsetMs": 200}],
            },
            {"tStartMs": 2000, "dDurationMs": 500, "segs": []},
        ]
    }
    assert parse_json3(data) == ([CapWord(text="real", start=0.3, end=1.0)], [])


def test_parse_json3_laughter_event():
    words, events = parse_json3(SAMPLE)
    assert events == [CapEvent(kind="laughter", start=6.0, end=7.5)]
    assert not any("aughter" in w.text for w in words)


@pytest.mark.parametrize(
    ("tag", "kind"),
    [
        ("[Laughter]", "laughter"),
        ("[LAUGHTER]", "laughter"),
        ("[laughs]", "laughter"),
        ("[Applause]", "applause"),
        ("[Music]", "music"),
        ("(Laughter)", "laughter"),  # how TED's own captions write it
        ("[audience laughing]", "laughter"),
        ("♪", "music"),
    ],
)
def test_sound_tags_become_events_never_words(tag, kind):
    words, events = parse_json3(one_event(f"{tag} hello"))
    assert events == [CapEvent(kind=kind, start=1.0, end=2.0)]
    assert words == [CapWord(text="hello", start=2.0, end=3.0)]


def test_a_word_stops_where_a_sound_tag_starts():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 4000, "segs": [{"utf8": "funny"}]},
            {"tStartMs": 1500, "dDurationMs": 1000, "segs": [{"utf8": "[Laughter]"}]},
        ]
    }
    words, events = parse_json3(data)
    assert words == [CapWord(text="funny", start=0.0, end=1.5)]
    assert events == [CapEvent(kind="laughter", start=1.5, end=2.5)]


def test_overlapping_events_read_in_time_order():
    # Two speakers: the second line starts while the first is still on screen.
    data = {
        "events": [
            {
                "tStartMs": 0,
                "dDurationMs": 4000,
                "segs": [{"utf8": "one"}, {"utf8": " three", "tOffsetMs": 2000}],
            },
            {"tStartMs": 1000, "dDurationMs": 1000, "segs": [{"utf8": "two"}]},
        ]
    }
    words, _ = parse_json3(data)
    assert [(w.text, w.start, w.end) for w in words] == [
        ("one", 0.0, 1.0),
        ("two", 1.0, 2.0),
        ("three", 2.0, 4.0),
    ]


def test_cues_and_marks_are_not_words():
    words, events = parse_json3(one_event(">> [door slams] (inaudible) - okay", duration_ms=5000))
    assert [w.text for w in words] == ["okay"] and events == []
    assert words[0].start == pytest.approx(5.0)  # the cues still take their share of the time


def test_bleeped_word_stays_a_word():
    words, _ = parse_json3(one_event("what the [ __ ] is that"))
    assert [w.text for w in words] == ["what", "the", "[ __ ]", "is", "that"]


def test_manual_caption_line_splits_its_time_evenly():
    words, _ = parse_json3(one_event("Hello there,\nhow are you?", start_ms=0, duration_ms=5000))
    assert [w.text for w in words] == ["Hello", "there,", "how", "are", "you?"]
    assert [w.start for w in words] == pytest.approx([0, 1, 2, 3, 4])
    assert [w.end for w in words] == pytest.approx([1, 2, 3, 4, 5])


def test_segs_sharing_a_start_read_as_one_stretch():
    # Manual captions split a line into styled segs that carry no offsets of their own.
    data = {
        "events": [
            {
                "tStartMs": 0,
                "dDurationMs": 3000,
                "segs": [{"utf8": "big "}, {"utf8": "news"}, {"utf8": " today"}],
            }
        ]
    }
    words, _ = parse_json3(data)
    assert [(w.text, w.start) for w in words] == [("big", 0.0), ("news", 1.0), ("today", 2.0)]


def test_parse_json3_survives_odd_shapes():
    assert parse_json3({}) == ([], [])
    assert parse_json3({"events": None}) == ([], [])
    assert parse_json3({"events": ["junk", {"segs": "junk"}, {"segs": ["junk", {"utf8": None}]}]}) == ([], [])


# Comment timestamps


def test_comment_timestamps():
    assert comment_timestamps("at 1:02:03 and 12:34", 4000) == [3723, 754]
    assert comment_timestamps("0:45", 4000) == [45]
    assert comment_timestamps("12:345", 4000) == []
    assert comment_timestamps("3:61", 4000) == []
    assert comment_timestamps("10:00 and 9:59", 600) == [599]  # at or past the end is dropped
    assert comment_timestamps("no time here, just 2 cents", 600) == []


def test_comment_with_three_timestamps_ignored():
    chapters = "0:00 intro 1:30 setup 2:45 the story"
    assert comment_timestamps(chapters, 600) == []
    assert comment_hotspots([Comment(text=chapters, likes=5000)], 600) == []


def test_hotspots_weight_and_hot_flag():
    comments = [
        Comment(text="2:00 lol", likes=0),
        Comment(text="2:00!!", likes=3),
        Comment(text="2:05 omg", likes=15),
    ]
    [spot] = comment_hotspots(comments, 600)
    assert isinstance(spot, Hotspot)
    assert (spot.start, spot.end, spot.count, spot.hot) == (120, 135, 3, True)
    assert spot.weight == pytest.approx(3 + math.sqrt(3) + math.sqrt(15))


def test_hot_needs_a_weight_of_three_and_the_top_tenth():
    comments = [
        Comment(text="1:00", likes=0),  # weight 1: under 3, never hot
        Comment(text="3:00", likes=9),  # weight 4: over 3 but under the 90th percentile
        Comment(text="5:00", likes=100),  # weight 11
    ]
    spots = comment_hotspots(comments, 600)
    assert [(s.start, s.count, s.weight, s.hot) for s in spots] == [
        (60, 1, 1.0, False),
        (180, 1, 4.0, False),
        (300, 1, 11.0, True),
    ]


def test_a_comment_counts_once_per_bucket():
    spots = comment_hotspots([Comment(text="2:00 and again at 2:03", likes=4)], 600)
    assert [(s.start, s.count, s.weight, s.hot) for s in spots] == [(120, 1, 3.0, True)]


def test_a_lone_light_bucket_is_not_hot():
    # It is its own 90th percentile, but a weight under 3 is never hot.
    [spot] = comment_hotspots([Comment(text="2:00", likes=1)], 600)
    assert (spot.weight, spot.hot) == (2.0, False)


def test_last_bucket_ends_at_the_video_end():
    spots = comment_hotspots([Comment(text="9:55", likes=4)], 598)
    assert (spots[0].start, spots[0].end) == (585, 598)


# Live chat


def test_parse_live_chat():
    # Three text messages and one paid one count; the membership event and the broken line don't.
    assert parse_live_chat(FIXTURES / "live_chat_sample.json") == [1.5, 62.0, 64.25, 65.0]


def test_parse_live_chat_reads_chat_captured_live(tmp_path):
    # yt-dlp puts the offset beside the action, not inside it, when it records a chat as it happens.
    action = {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {"id": "x"}}}}
    line = {
        "replayChatItemAction": {"actions": [action, action]},
        "videoOffsetTimeMsec": "7000",
        "isLive": True,
    }
    path = tmp_path / "chat.live_chat.json"
    path.write_text(json.dumps(line) + "\n\n[]\n")
    assert parse_live_chat(path) == [7.0, 7.0]


def steady_chat(seconds: int = 600) -> list[float]:
    return [i + 0.5 for i in range(seconds)]


def test_chat_bursts():
    burst = [300 + i / 6 for i in range(60)]
    assert chat_bursts(steady_chat() + burst, 600) == [(295.0, 305.0)]
    assert chat_bursts([300 + i / 20 for i in range(199)], 600) == []  # under 200 messages


def test_touching_bursts_merge():
    burst = [300 + i / 6 for i in range(120)]  # 300 to 320 s
    assert chat_bursts(steady_chat() + burst, 600) == [(295.0, 315.0)]


def test_burst_at_the_start_is_clamped_at_zero():
    burst = [i / 6 for i in range(60)]
    assert chat_bursts(steady_chat() + burst, 600) == [(0.0, 5.0)]


def test_chat_outside_the_video_does_not_count():
    early = [-30.0] * 300  # people chatting before the stream began
    assert chat_bursts(early + [300 + i / 20 for i in range(150)], 600) == []
