"""What every brain pass works from: the source, its transcript and signals, the project's options and
settings, the LLM, and what happens when the LLM can't answer (SPEC 9.2; SPEC 5, call 17)."""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from clipforge.brain.llm.base import LLMProvider, LLMRequest, call_structured
from clipforge.brain.llm.cache import LLMCache
from clipforge.config import Settings, model_for
from clipforge.errors import LLMError, WaitUntil
from clipforge.models import LENGTH_RANGES, ProjectOptions, Signals, SourceMeta, Transcript

LIMIT_KINDS = frozenset({"usage_limit", "auth"})  # not a failed call: the project waits or goes Basic
LIMIT_WAIT_S = 3600.0  # a usage limit that doesn't say when it lifts is tried again in an hour
LOGIN_WAIT_S = 600.0
FOCUS_REACH_S = 180.0  # a timestamp in the link asks for the best moment within 3 minutes of it
PROMPTS = Path(__file__).parent / "prompts"


def load_prompt(name: str, **values: object) -> str:
    """``prompts/<name>.md`` with each ``{key}`` given filled in. Other braces stay as they are."""
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace(f"{{{key}}}", str(value))
    return text.strip()


def _no_progress(fraction: float, message: str) -> None:
    pass


@dataclass
class BrainContext:
    project_id: str
    meta: SourceMeta
    transcript: Transcript
    signals: Signals
    options: ProjectOptions
    settings: Settings
    provider: LLMProvider | None  # None is Basic mode
    provider_name: str
    cache: LLMCache | None = None
    log_dir: Path | None = None
    rights_basis: str = "not_cleared"
    focus_s: float | None = None
    progress: Callable[[float, str], None] = _no_progress
    notes: list[str] = field(default_factory=list)  # about the whole run, such as "usage_limit"
    llm_disabled: bool = False  # a limit switched the rest of the run to Basic mode
    limit_message: str | None = None  # what that limit said, to show with the clips
    carry_on: str | None = None  # what a wait says happens at {when}; by default the run carries on then
    lengths: tuple[float, float] | None = None  # a linked campaign's clip lengths, over the option's
    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(1), init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def length_range(self) -> tuple[float, float]:
        return self.lengths or LENGTH_RANGES[self.options.length]

    def model(self, pass_name: str) -> str:
        return model_for(self.settings, self.provider_name, pass_name)

    def next_id(self) -> str:
        """The next candidate id: k1, k2, ... whichever pass or thread asks."""
        with self._lock:
            return f"k{next(self._ids)}"

    def note(self, text: str) -> None:
        with self._lock:
            if text not in self.notes:
                self.notes.append(text)

    def on_llm_error(self, err: LLMError) -> None:
        """What a pass does before it falls back. A usage limit or a login problem pauses the project until
        it can carry on (WaitUntil), or with on_llm_limit "basic" turns the LLM off for the rest of the run.
        Any other error is only noted."""
        if err.kind in LIMIT_KINDS and self.settings.on_llm_limit == "wait":
            wait = LIMIT_WAIT_S if err.kind == "usage_limit" else LOGIN_WAIT_S
            until = err.retry_at or time.time() + wait
            then = (
                self.carry_on.replace("{when}", _when(until))
                if self.carry_on
                else f"ClipForge will carry on at {_when(until)}, or run clipforge rerun {self.project_id}"
                " --provider heuristic for Basic mode now."
            )
            raise WaitUntil(until, f"{err.message} {then}")
        if err.kind in LIMIT_KINDS:
            with self._lock:
                if not self.llm_disabled:
                    self.llm_disabled, self.limit_message = True, err.message
        self.note(err.kind)


def ask_all[T: BaseModel](
    ctx: BrainContext,
    requests: list[LLMRequest],
    out_model: type[T],
    message: str,
    *,
    sizes: list[int] | None = None,
) -> list[T | LLMError | None]:
    """Each request's answer, in the order given, asked ``llm_parallel`` at a time: the output, the LLMError
    it ended with, or None when a usage limit or a login problem came first and it was never asked. Each
    request answered moves the progress on by its size (the clips in a batch; 1 by default), shown with
    ``message`` filled in from ``{done}`` and ``{total}``. Once every request has finished, a limit goes to
    on_llm_error, which waits or turns the LLM off; other errors are the caller's to handle."""
    sizes = sizes if sizes is not None else [1] * len(requests)
    total = sum(sizes)
    stop = threading.Event()

    def ask(req: LLMRequest) -> T | LLMError | None:
        if stop.is_set():
            return None
        try:
            return call_structured(ctx.provider, req, out_model, cache=ctx.cache, log_dir=ctx.log_dir)
        except LLMError as err:
            if err.kind in LIMIT_KINDS:
                stop.set()  # in this thread, before a free worker takes the next request
            return err

    answers: dict[int, T | LLMError | None] = {}
    with ThreadPoolExecutor(max_workers=max(1, ctx.settings.llm_parallel)) as pool:
        futures = {pool.submit(ask, req): k for k, req in enumerate(requests)}
        done = 0
        try:
            for future in as_completed(futures):
                k = futures[future]
                answer = answers[k] = future.result()
                if answer is not None:
                    done += sizes[k]
                    ctx.progress(done / total, message.format(done=done, total=total))
        except BaseException:
            stop.set()  # the requests still queued are skipped
            raise
    in_order = [answers[k] for k in range(len(requests))]
    limit = next((a for a in in_order if isinstance(a, LLMError) and a.kind in LIMIT_KINDS), None)
    if limit is not None:
        ctx.on_llm_error(limit)  # waits, or turns the LLM off for the rest of the run
    return in_order


def _when(ts: float) -> str:
    """``3:37 pm``, ``12:05 am tomorrow`` or ``9:00 am on Thursday 22 Oct`` (a weekly limit can be days
    away), in local time."""
    at = datetime.fromtimestamp(ts)
    clock = f"{at.hour % 12 or 12}:{at.minute:02d} {'am' if at.hour < 12 else 'pm'}"
    days = (at.date() - datetime.now().date()).days
    if days <= 0:
        return clock
    if days == 1:
        return f"{clock} tomorrow"
    return f"{clock} on {at:%A} {at.day} {at:%b}"
