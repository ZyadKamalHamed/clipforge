"""Where ClipForge keeps its data (SPEC 6.3). Everything lives under one home folder."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def source(self) -> Path:
        return self.root / "source"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def clips(self) -> Path:
        return self.root / "clips"

    @property
    def info_json(self) -> Path:
        return self.source / "info.json"

    @property
    def meta_json(self) -> Path:
        return self.source / "meta.json"

    @property
    def thumb(self) -> Path:
        """The source's picture on its card in the window (SPEC 14.3)."""
        return self.source / "thumb.jpg"

    @property
    def comments_json(self) -> Path:
        return self.source / "comments.json"

    @property
    def live_chat_json(self) -> Path:
        return self.source / "chat.live_chat.json"

    @property
    def captions_glob(self) -> str:
        """Relative to root: ``pp.root.glob(pp.captions_glob)``."""
        return "source/subs.*.json3"

    @property
    def audio16k(self) -> Path:
        return self.work / "audio16k.wav"

    @property
    def transcript_json(self) -> Path:
        return self.work / "transcript.json"

    @property
    def signals_json(self) -> Path:
        return self.work / "signals.json"

    @property
    def candidates_json(self) -> Path:
        return self.work / "candidates.json"

    @property
    def llm_dir(self) -> Path:
        return self.work / "llm"

    @property
    def sections(self) -> Path:
        """Downloads of single clips' ranges, when the whole video isn't downloaded (SPEC 7.2)."""
        return self.source / "sections"

    def clip_dir(self, clip_id: str) -> Path:
        return self.clips / clip_id

    def analysis_json(self, clip_id: str) -> Path:
        return self.clip_dir(clip_id) / "analysis.json"

    def render_dir(self, clip_id: str, version: int) -> Path:
        return self.clip_dir(clip_id) / f"v{version}"

    def ensure(self) -> None:
        for folder in (self.source, self.work, self.clips, self.llm_dir):
            folder.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Paths:
    home: Path

    @classmethod
    def from_env(cls) -> Paths:
        env = os.environ.get("CLIPFORGE_HOME")
        return cls(Path(env).expanduser() if env else Path.home() / "ClipForge")

    @property
    def db(self) -> Path:
        return self.home / "clipforge.db"

    @property
    def projects(self) -> Path:
        return self.home / "projects"

    @property
    def assets(self) -> Path:
        return self.home / "assets"

    @property
    def hf_home(self) -> Path:
        return self.assets / "models" / "hf"

    @property
    def library(self) -> Path:
        return self.home / "library"

    @property
    def models(self) -> Path:
        return self.assets / "models"

    @property
    def emoji(self) -> Path:
        return self.assets / "emoji"

    @property
    def gameplay(self) -> Path:
        return self.library / "gameplay"

    @property
    def music(self) -> Path:
        return self.library / "music"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def run_dir(self) -> Path:
        return self.home / "run"

    @property
    def uploads(self) -> Path:
        """Files dropped on the window, each in a folder of its own until its project has cloned it."""
        return self.home / "uploads"

    @property
    def browser(self) -> Path:
        """The posting accounts' Chrome profiles, one folder each (SPEC 10.3)."""
        return self.home / "browser"

    @property
    def accounts(self) -> Path:
        """Each linked account's own files, such as its avatar."""
        return self.home / "accounts"

    @property
    def exports(self) -> Path:
        """Posts exported for posting by hand: the video and its caption (SPEC 10.6)."""
        return self.home / "exports"

    @property
    def post_logs(self) -> Path:
        """Each post's step log and screenshots."""
        return self.logs / "posts"

    def ensure(self) -> None:
        for folder in (self.projects, self.assets, self.hf_home, self.library, self.logs, self.run_dir):
            folder.mkdir(parents=True, exist_ok=True)

    def project(self, pid: str) -> ProjectPaths:
        return ProjectPaths(self.projects / pid)
