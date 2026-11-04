"""Claude Code as the brain (SPEC 9.2): the claude command in print mode on the person's own login and
plan. It runs with no tools, no settings, no MCP servers and no saved session, so a call can only answer,
and without the API key variables, so the plan pays rather than an API account."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from clipforge import children
from clipforge.brain.llm.base import DETAILS_CHARS, LLMRequest, LLMResponse
from clipforge.errors import LLMError

CLAUDE_MISSING = "Claude Code isn't installed. Install it, then run claude in Terminal and log in."
META_KEYS = ("total_cost_usd", "duration_ms", "usage", "modelUsage")
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
KILL_WAIT_S = 5.0
RESET_MARGIN_S = 120  # come back just after the stated reset, not on it
RESET_GRACE_S = 600  # a reset that is a little late is asked about again soon, not tomorrow


def find_claude(
    which: Callable[[str], str | os.PathLike | None] | None = None, user_home: Path | None = None
) -> str | None:
    """The claude command: on the PATH, else where Claude Code's installer puts it (an app opened from
    Finder, or a Terminal set up without ~/.local/bin, doesn't have it on the PATH). which is looked up
    when called, so a patched shutil.which applies."""
    found = (which or shutil.which)("claude")
    if found:
        return str(found)
    local = (user_home or Path.home()) / ".local" / "bin" / "claude"
    return str(local) if local.is_file() else None


class ClaudeCLIProvider:
    name = "claude_cli"

    def __init__(self, binary: str | None = None, *, workdir: Path, strip_api_key: bool = True):
        self.binary = binary or find_claude()
        if self.binary is None:
            raise LLMError("unavailable", CLAUDE_MISSING)
        self.workdir = Path(workdir)
        self.strip_api_key = strip_api_key

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        argv = [
            self.binary, "-p", "--output-format", "json", "--json-schema", json.dumps(req.schema),
            "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
            "--model", req.model, "--system-prompt", req.system,
        ]  # fmt: skip
        env = dict(os.environ)
        if self.strip_api_key:
            for name in STRIPPED_ENV:
                env.pop(name, None)
        self.workdir.mkdir(parents=True, exist_ok=True)
        try:
            proc = children.popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.workdir,
                env=env,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,  # its own process group, so a timeout takes its helpers with it
            )
        except OSError as err:
            raise LLMError("unavailable", CLAUDE_MISSING, details=str(err)) from err
        with proc:
            try:  # the prompt goes on stdin
                stdout, stderr = proc.communicate(req.prompt, timeout=req.timeout_s)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.communicate(timeout=KILL_WAIT_S)
                raise LLMError("timeout", details=f"no answer in {req.timeout_s:g} s") from None
        return _read_output(proc.returncode, stdout, stderr)


def _read_output(code: int, stdout: str, stderr: str) -> LLMResponse:
    """The answer in a finished run's output, or the LLMError its words describe."""
    try:
        body = json.loads(stdout)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = None
    said = _said(body) if body is not None else stdout
    text = "\n".join(part for part in (said.strip(), stderr.strip()) if part)
    details = f"exit {code}: {(text or stdout.strip())[:DETAILS_CHARS]}"
    if body is not None and code == 0 and not body.get("is_error"):
        data = body.get("structured_output")
        if isinstance(data, dict):
            return LLMResponse(data, {key: body[key] for key in META_KEYS if key in body})
        raise LLMError("bad_output", details=f"finished without a structured answer; {details}")
    if body is not None and str(body.get("subtype", "")).startswith("error_max_structured_output"):
        raise LLMError("bad_output", details=details)
    kind = _classify(text)
    if kind == "other" and code == 0:
        kind = "bad_output"  # it finished, but with nothing ClipForge can use
    retry_at = parse_reset_time(text, datetime.now().astimezone()) if kind == "usage_limit" else None
    raise LLMError(kind, details=details, retry_at=retry_at)


def _said(body: dict) -> str:
    """The words of a run's result: its final message and any errors it lists."""
    errors = body.get("errors")
    parts = [body.get("result"), *(errors if isinstance(errors, list) else [])]
    return "\n".join(part for part in parts if isinstance(part, str) and part.strip())


USAGE_WORDS = ("usage limit", "session limit", "weekly limit", "hit your limit", "resets")
AUTH_WORDS = ("not logged in", "/login", "invalid api key", "authentication")
BUSY_WORDS = ("overloaded", "rate limit", "rate_limit")


def _classify(text: str) -> str:
    low = text.lower()
    if any(w in low for w in USAGE_WORDS) or ("limit reached" in low and "rate limit" not in low):
        return "usage_limit"
    if any(w in low for w in AUTH_WORDS):
        return "auth"
    if any(w in low for w in BUSY_WORDS) or re.search(r"\b529\b", low):
        return "rate_limit"
    return "other"


# When a usage limit lifts

CLOCK = re.compile(
    r"\bresets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?m\b\.?)?"  # resets at 3:30am
    r"(?:\s*\(([^)]*)\))?",  # (Australia/Sydney)
    re.I,
)
WAIT = re.compile(
    r"\bin\s+(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m)\b"  # in 2 hours
    r"(?:\s*(?:and\s+)?(\d+)\s*(?:minutes?|mins?|m)\b)?",  # and 30 minutes
    re.I,
)
UNIT_S = {"d": 86400, "h": 3600, "m": 60}


def parse_reset_time(text: str, now: datetime) -> float | None:
    """When the limit in ``text`` lifts, as a timestamp: the next time its clock time comes round, in its
    zone (else ``now``'s), plus two minutes; or ``now`` plus the wait it states. None when it gives
    neither."""
    match = CLOCK.search(text)
    clock = _clock(*match.group(1, 2, 3)) if match else None
    if clock is not None:
        zone = _zone(match[4]) or now.tzinfo or now.astimezone().tzinfo
        local = now.astimezone(zone)
        at = datetime.combine(local.date(), clock, tzinfo=zone)
        if at <= local - timedelta(seconds=RESET_GRACE_S):
            at = datetime.combine(local.date() + timedelta(days=1), clock, tzinfo=zone)
        elif at <= local:
            return now.timestamp() + RESET_MARGIN_S
        return at.timestamp() + RESET_MARGIN_S
    if match := WAIT.search(text):
        seconds = int(match[1]) * UNIT_S[match[2][0].lower()] + int(match[3] or 0) * 60
        return now.timestamp() + seconds
    return None


def _clock(hour: str, minute: str | None, half: str | None) -> time | None:
    """The clock time, or None when it is none: a bare number, 13pm, 3:75."""
    h, m = int(hour), int(minute or 0)
    if half:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if half.lower() == "p" else 0)
    elif minute is None or h > 23:
        return None
    return time(h, m) if m <= 59 else None


def _zone(name: str | None) -> ZoneInfo | None:
    if not name or not name.strip():
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):  # an unknown zone reads as local time
        return None
