"""What every speech engine shares (SPEC 7.3): the word and result shapes, choosing an engine, and
running an engine's model in a worker process, so a crash or a stuck model never takes ClipForge down
and the model's memory goes back to the system when it finishes. The worker side is
clipforge.asr.worker."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from clipforge import children
from clipforge.errors import ToolError, ToolMissing
from clipforge.media.audio import wav_seconds
from clipforge.media.ffmpeg import HOMEBREW_BIN, _tail, ffmpeg_bin

log = logging.getLogger(__name__)

PARAKEET_LANGS = {
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
    "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
}  # fmt: skip
LANGUAGE_WARNING = (
    "This video's language isn't one the default speech model knows. "
    "Install the Whisper add-on for better transcripts."
)
SETTING_WARNING = "The speech engine chosen in Settings isn't available, so ClipForge picked one itself."
FAILED = "Speech to text failed."
TIMEOUT = "Speech to text took too long and was stopped."
EXIT_MODEL = 3  # the worker's exit code when the model is neither cached nor downloadable
PREPARE_TIMEOUT_S = 3 * 3600.0  # 2.5 GB in three hours needs under 2 Mbit/s
# The only settings the worker gets from this environment, so no key, token or password ever reaches it.
PASS_THROUGH = {
    "PATH", "HOME", "TMPDIR", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TZ",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
}  # fmt: skip
PROXIES = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}  # either case, for the model download


class RawWord(BaseModel):
    text: str
    start: float
    end: float
    conf: float | None = None


class ASRResult(BaseModel):
    engine: str
    engine_version: str
    language: str | None
    words: list[RawWord]


class SpeechEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def transcribe(
        self,
        wav: Path,
        *,
        language: str | None = None,
        window: tuple[float, float] | None = None,
        beam: bool = False,
        timeout_s: float | None = None,
    ) -> ASRResult: ...


def base_language(code: str | None) -> str | None:
    """The language part of a code: "en-US" and "en_us" are "en"."""
    if not code:
        return None
    return re.split(r"[-_]", code, maxsplit=1)[0].lower() or None


def choose_engine(
    language: str | None, setting: str, available: dict[str, bool], has_captions: bool
) -> tuple[str, str | None]:
    """The engine to use and a warning to show, if any. An available engine chosen in Settings wins;
    otherwise Parakeet for its 25 languages (and an unknown one), then Whisper, then YouTube's
    captions, then Parakeet anyway. The last two warn that Whisper would do better."""
    usable = {name for name, ok in available.items() if ok} | ({"captions"} if has_captions else set())
    if setting != "auto" and setting in usable:
        return setting, None
    warning = None if setting == "auto" else SETTING_WARNING
    code = base_language(language)
    if code is None or code in PARAKEET_LANGS:
        return "parakeet", warning
    if "whisper" in usable:
        return "whisper", warning
    warning = f"{warning} {LANGUAGE_WARNING}" if warning else LANGUAGE_WARNING
    return ("captions" if has_captions else "parakeet"), warning


def worker_timeout_s(audio_seconds: float) -> float:
    return 120 + 0.5 * audio_seconds


def worker_env(hf_home: Path) -> dict[str, str]:
    """The worker's environment: a few plain settings from this one (never a key, a token or an outside
    Hugging Face setting), ClipForge's own model cache, and ffmpeg first on PATH."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name in PASS_THROUGH or name.startswith("LC_") or name.lower() in PROXIES
    }
    hf_home = Path(hf_home)
    env.update(
        HF_HOME=str(hf_home),
        HF_HUB_CACHE=str(hf_home / "hub"),
        HF_HUB_DISABLE_TELEMETRY="1",
        HF_HUB_DISABLE_PROGRESS_BARS="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
    )
    try:
        tools = str(Path(ffmpeg_bin()).parent)
    except ToolMissing:
        tools = str(HOMEBREW_BIN)
    rest = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry and entry != tools]
    env["PATH"] = os.pathsep.join([tools, *rest])
    return env


def _drop_partial_downloads(hub: Path) -> None:
    """Delete the part files a stopped download leaves behind: huggingface_hub never resumes them."""
    if hub.is_dir():
        for part in hub.rglob("*.incomplete"):
            part.unlink(missing_ok=True)


class WorkerEngine:
    """A speech engine whose model runs in the worker. Subclasses name the engine, its model and the files
    the model needs; the model lives in the Hugging Face cache under ``hf_home``."""

    name: str
    files: tuple[str, ...]
    size: str  # the model download, for messages
    missing_code: str
    missing_message: str

    def __init__(self, hf_home: Path, *, model_id: str, revision: str) -> None:
        self.hf_home = Path(hf_home)
        self.model_id = model_id
        self.revision = revision

    def available(self) -> bool:
        raise NotImplementedError

    def model_dir(self) -> Path:
        """Where the Hugging Face cache keeps this model's files at the pinned revision."""
        repo = "models--" + self.model_id.replace("/", "--")
        return self.hf_home / "hub" / repo / "snapshots" / self.revision

    def model_ready(self) -> bool:
        return all((self.model_dir() / name).is_file() for name in self.files)

    def prepare(self, *, timeout_s: float = PREPARE_TIMEOUT_S) -> None:
        """Fetch the model into the cache when it isn't all there yet (once, a large download)."""
        if self.model_ready():
            return
        log.info("fetching the speech model %s@%s into %s", self.model_id, self.revision, self.hf_home)
        with tempfile.TemporaryDirectory(prefix="clipforge-asr-") as tmp:
            try:
                self._run([*self._command(), "--prepare"], cwd=tmp, timeout_s=timeout_s)
            except subprocess.TimeoutExpired as err:
                _drop_partial_downloads(self.hf_home / "hub")
                raise ToolError(
                    "asr_model_timeout",
                    f"Downloading the speech model ({self.size}) took too long and was stopped. "
                    "Check the internet connection and try again.",
                    details=f"stopped after {timeout_s:g} s\n{_tail(err.stderr)}",
                    retryable=True,
                ) from err
        if not self.model_ready():
            raise self._model_missing(f"the download finished but {self.model_dir()} lacks {self.files}")

    def transcribe(
        self,
        wav: Path,
        *,
        language: str | None = None,
        window: tuple[float, float] | None = None,
        beam: bool = False,
        timeout_s: float | None = None,
    ) -> ASRResult:
        windows = None if window is None else [window]
        [result] = self.transcribe_many(wav, windows, language=language, beam=beam, timeout_s=timeout_s)
        return result

    def transcribe_many(
        self,
        wav: Path,
        windows: list[tuple[float, float]] | None = None,
        *,
        language: str | None = None,
        beam: bool = False,
        timeout_s: float | None = None,
    ) -> list[ASRResult]:
        """Hear the whole file (``windows`` None) or each window, all in one worker run so the model
        loads once. Times are seconds in the whole file either way."""
        if windows is not None and not windows:
            return []
        if not self.available():
            raise ToolMissing(self.missing_code, self.missing_message)
        self.prepare()
        wav = Path(wav).resolve()
        code = base_language(language)
        if windows is None:
            seconds = wav_seconds(wav)
        else:
            seconds = sum(max(0.0, end - start) for start, end in windows)
        with tempfile.TemporaryDirectory(prefix="clipforge-asr-") as tmp:
            out = Path(tmp) / "heard.json"
            cmd = [*self._command(), "--wav", str(wav), "--out", str(out)]
            if code:
                cmd += ["--language", code]
            if beam:
                cmd.append("--beam")
            for start, end in windows or []:
                cmd += ["--start", str(float(start)), "--end", str(float(end))]
            try:
                self._run(
                    cmd, cwd=tmp, timeout_s=worker_timeout_s(seconds) if timeout_s is None else timeout_s
                )
            except subprocess.TimeoutExpired as err:
                raise ToolError(
                    "asr_timeout", TIMEOUT, details=f"stopped after {err.timeout} s\n{_tail(err.stderr)}"
                ) from err
            return self._results(out, len(windows) if windows else 1, code)

    def _command(self) -> list[str]:
        cmd = [sys.executable, "-I", "-m", "clipforge.asr.worker", "--engine", self.name]
        cmd += ["--model-id", self.model_id, "--revision", self.revision]
        for name in self.files:
            cmd += ["--file", name]
        return cmd

    def _run(self, cmd: list[str], *, cwd: str, timeout_s: float) -> None:
        """Run the worker and turn a failing exit code into an error. A timeout is raised as is."""
        log.debug("speech worker: %s (timeout %.0f s)", shlex.join(cmd), timeout_s)
        proc = children.run(
            cmd,
            cwd=cwd,
            env=worker_env(self.hf_home),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        if proc.returncode == EXIT_MODEL:
            raise self._model_missing(_tail(proc.stderr))
        if proc.returncode != 0:
            raise ToolError(
                "asr_failed", FAILED, details=f"exit code {proc.returncode}\n{_tail(proc.stderr)}"
            )

    def _model_missing(self, details: str) -> ToolError:
        return ToolError(
            "asr_model_missing",
            f"The speech model ({self.size}) isn't on this Mac yet and couldn't be downloaded. "
            "Check the internet connection and try again.",
            details=details,
            retryable=True,
        )

    def _results(self, out: Path, count: int, language: str | None) -> list[ASRResult]:
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
            pieces = data["results"]
            if len(pieces) != count:
                raise ValueError(f"{len(pieces)} results for {count} pieces of audio")
            return [
                ASRResult(
                    engine=self.name,
                    engine_version=str(data["engine_version"]),
                    language=data.get("language") or language,
                    words=[RawWord.model_validate(word) for word in words],
                )
                for words in pieces
            ]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
            raise ToolError(
                "asr_failed", FAILED, details=f"the speech worker's result can't be read: {err}"
            ) from err
