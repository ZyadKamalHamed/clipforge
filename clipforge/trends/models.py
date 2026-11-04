"""Sounds, hashtag sets and list reads (SPEC 13, 15). Times are epoch seconds."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

STALE_DAYS = 14  # a sound added longer ago than this is marked stale (SPEC 13)

SoundSource = Literal["shortlist", "ig_trending"]
AudioType = Literal["music", "original_sound"]
Region = Literal["AU", "US"]


class Sound(BaseModel):
    """A TikTok sound you shortlisted, or one of the trending sounds Instagram lists for an account: its
    title and id, never the sound itself."""

    id: str
    platform: Literal["tiktok", "instagram"]
    source: SoundSource
    external_id: str
    title: str
    artist: str | None = None
    url: str | None = None
    region: Region | None = None
    mood_tags: list[str] = []
    energy: int | None = Field(None, ge=1, le=5)
    audio_type: AudioType | None = None  # Instagram's lists only
    duration_s: float | None = None
    rank: int | None = None  # its place in Instagram's list, 0 first
    account_id: str | None = None  # the account Instagram listed it for
    fetched_at: float | None = None
    created_at: float

    def stale(self, now: float) -> bool:
        return now - self.created_at > STALE_DAYS * 86400


class PostSound(BaseModel):
    """The sound picked for one post: an Instagram trending sound Instagram adds through
    ``audio_configuration`` at ``volume``, or a shortlisted TikTok sound you add yourself in TikTok's
    window."""

    source: SoundSource
    external_id: str
    title: str
    artist: str | None = None
    url: str | None = None
    volume: int = Field(15, ge=0, le=100)


class HashtagSet(BaseModel):
    id: str
    niche: str
    name: str
    tags: list[str] = []
    created_at: float
    updated_at: float


class TrendRead(BaseModel):
    """When a list was last read: ``ig:<account id>`` for an account's trending sounds, ``feed:socialpilot``
    for the weekly ping, whose ``newest_at`` is the feed's newest post and ``seen_at`` when you last opened a
    list."""

    id: str
    read_at: float | None = None
    newest_at: float | None = None
    seen_at: float | None = None
    result: Literal["ok", "error", "blocked"] | None = None
    error: str | None = None
