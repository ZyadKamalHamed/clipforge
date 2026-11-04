"""YouTube's own signals: word timed captions (json3), comment timestamps and live chat bursts (SPEC 7.4)."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel

from clipforge.models import Comment


class CapWord(BaseModel):
    text: str
    start: float
    end: float


class CapEvent(BaseModel):
    kind: Literal["laughter", "applause", "music"]
    start: float
    end: float


class Hotspot(BaseModel):
    start: float
    end: float
    count: int
    weight: float
    hot: bool


# Captions

# A bracketed or parenthesized cue is one token even with spaces inside, like "[door slams]".
_TOKEN = re.compile(r"\[[^\]]*\]|\([^)]*\)|\S+")
_SOUNDS = (("laugh", "laughter"), ("applau", "applause"), ("music", "music"))
_NOTES = set(chr(0x266A) + chr(0x266B))


def _is_cue(token: str) -> bool:
    return token[0] in "[(" and token[-1] in "])"


def _sound(token: str) -> str | None:
    """The sound a caption cue names: [Laughter], (Applause), [audience laughing], a music note."""
    if set(token) <= _NOTES:
        return "music"
    if _is_cue(token):
        inner = token[1:-1].lower()
        return next((kind for key, kind in _SOUNDS if key in inner), None)
    return None


def _is_word(token: str) -> bool:
    if _is_cue(token):
        return set(token[1:-1].strip()) == {"_"}  # YouTube's "[ __ ]" for a bleeped word, still a word
    return any(ch.isalnum() for ch in token)  # drops speaker marks like ">>" and "-"


def _tokens(event: dict) -> list[tuple[float, float, str]]:
    """(start ms, end of its share ms, text) for each token of a caption event. Segs that share a start
    are one stretch of text, and a stretch's words split its time evenly."""
    t0 = event.get("tStartMs") or 0
    stretches: dict[float, str] = {}
    for seg in event["segs"]:
        if isinstance(seg, dict) and isinstance(seg.get("utf8"), str):
            start = t0 + (seg.get("tOffsetMs") or 0)
            stretches[start] = stretches.get(start, "") + seg["utf8"]
    timed = [(start, _TOKEN.findall(stretches[start])) for start in sorted(stretches)]
    timed = [(start, found) for start, found in timed if found]
    event_end = t0 + (event.get("dDurationMs") or 0)
    out = []
    for i, (start, found) in enumerate(timed):
        stop = timed[i + 1][0] if i + 1 < len(timed) else max(event_end, start)
        share = (stop - start) / len(found)
        out += [(start + j * share, start + (j + 1) * share, token) for j, token in enumerate(found)]
    return out


def parse_json3(source: Path | dict) -> tuple[list[CapWord], list[CapEvent]]:
    """Timed words, and laughter, applause and music events, from a YouTube json3 caption file.

    A word ends where the next word or cue starts, but never after its own event ends. Cues that name
    no sound, like [door slams], and marks like ">>" are neither words nor events."""
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    events = data.get("events") if isinstance(data, dict) else None
    tokens: list[tuple[float, float, float, str]] = []  # start, share end, event end, text (ms)
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or not isinstance(event.get("segs"), list) or event.get("aAppend"):
            continue
        event_end = (event.get("tStartMs") or 0) + (event.get("dDurationMs") or 0)
        tokens += [(start, share_end, event_end, text) for start, share_end, text in _tokens(event)]
    tokens.sort(key=lambda token: token[0])

    words: list[CapWord] = []
    sounds: list[CapEvent] = []
    for i, (start, share_end, event_end, text) in enumerate(tokens):
        kind = _sound(text)
        if kind:
            sounds.append(CapEvent(kind=kind, start=start / 1000, end=share_end / 1000))
        elif _is_word(text):
            following = tokens[i + 1][0] if i + 1 < len(tokens) else event_end
            end = max(start, min(following, event_end))
            words.append(CapWord(text=text, start=start / 1000, end=end / 1000))
    return words, sounds


# Comments

_STAMP = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)(?![\d:])")
CHAPTER_LIST = 3  # a comment with this many timestamps lists chapters; it isn't reacting to a moment
HOT_WEIGHT = 3.0
HOT_PERCENTILE = 90


def comment_timestamps(text: str, duration_s: float) -> list[float]:
    """The moments a comment points at, in seconds, in the order it names them."""
    found = [int(h or 0) * 3600 + int(m) * 60 + int(s) for h, m, s in _STAMP.findall(text)]
    if len(found) >= CHAPTER_LIST:
        return []
    return [float(t) for t in found if t < duration_s]


def comment_hotspots(comments: list[Comment], duration_s: float, bucket_s: float = 15.0) -> list[Hotspot]:
    """Every bucket the comments point at. A comment adds 1 + sqrt(likes) to each bucket it names, once
    per bucket; a bucket is hot at a weight of 3 or more that also reaches the 90th percentile."""
    counts: dict[int, int] = {}
    weights: dict[int, float] = {}
    for comment in comments:
        for index in {int(t // bucket_s) for t in comment_timestamps(comment.text, duration_s)}:
            counts[index] = counts.get(index, 0) + 1
            weights[index] = weights.get(index, 0.0) + 1 + math.sqrt(max(comment.likes, 0))
    if not weights:
        return []
    cut = max(HOT_WEIGHT, float(np.percentile(list(weights.values()), HOT_PERCENTILE)))
    return [
        Hotspot(
            start=index * bucket_s,
            end=min((index + 1) * bucket_s, duration_s),
            count=counts[index],
            weight=weights[index],
            hot=weights[index] >= cut,
        )
        for index in sorted(weights)
    ]


# Live chat

_MESSAGES = ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer")


def _messages(actions: list) -> int:
    count = 0
    for action in actions:
        item = (action.get("addChatItemAction") or {}).get("item") or {}
        count += any(key in item for key in _MESSAGES)
    return count


def parse_live_chat(path: Path) -> list[float]:
    """One offset into the video, in seconds, per chat message (plain or paid) in yt-dlp's live chat
    file. Memberships, tickers and broken lines are skipped."""
    offsets: list[float] = []
    with Path(path).open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                row = json.loads(line)
                replay = row["replayChatItemAction"]
                # A replay keeps the offset inside the action; chat recorded live keeps it beside it.
                offset = float(replay.get("videoOffsetTimeMsec", row.get("videoOffsetTimeMsec"))) / 1000
                count = _messages(replay.get("actions") or [])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            offsets += [offset] * count
    return offsets


def chat_bursts(
    offsets: list[float],
    duration_s: float,
    *,
    window_s: float = 10.0,
    pct: float = 0.95,
    ratio: float = 3.0,
    shift_s: float = 5.0,
    min_messages: int = 200,
) -> list[tuple[float, float]]:
    """Stretches where the chat explodes. Messages are counted per 10 s window; a window bursts at or
    above the 95th percentile of the counts and at least three times the median. Bursts move 5 s
    earlier, because chat reacts after the moment, and bursts that touch merge."""
    inside = np.asarray([t for t in offsets if 0 <= t < duration_s], dtype=np.float64)
    if len(inside) == 0 or len(inside) < min_messages:
        return []
    per_window = np.bincount((inside // window_s).astype(int), minlength=math.ceil(duration_s / window_s))
    cut = max(float(np.percentile(per_window, pct * 100)), ratio * max(float(np.median(per_window)), 1.0))
    bursts: list[tuple[float, float]] = []
    for index in np.flatnonzero(per_window >= cut).tolist():
        start = max(0.0, index * window_s - shift_s)
        end = max(0.0, min((index + 1) * window_s, duration_s) - shift_s)
        if bursts and start <= bursts[-1][1]:
            bursts[-1] = (bursts[-1][0], end)
        else:
            bursts.append((start, end))
    return bursts
