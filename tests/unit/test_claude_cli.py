import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from clipforge.brain.llm import claude_cli
from clipforge.brain.llm.base import LLMRequest
from clipforge.brain.llm.claude_cli import ClaudeCLIProvider, parse_reset_time
from clipforge.errors import LLM_MESSAGES, LLMError

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "n": {"type": "integer"}},
    "required": ["answer", "n"],
    "additionalProperties": False,
}
SUCCESS = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "",
    "structured_output": {"answer": "yes", "n": 2},
    "total_cost_usd": 0.0123,
    "duration_ms": 1500,
    "usage": {"input_tokens": 10, "output_tokens": 4},
    "modelUsage": {"claude-sonnet": {"costUSD": 0.0123}},
    "session_id": "abc",
}


def request(prompt: str = "Pick one.", timeout_s: float = 30.0) -> LLMRequest:
    return LLMRequest("discover", "Be brief.", prompt, SCHEMA, "sonnet", timeout_s)


def fake_claude(tmp_path: Path, body: str) -> Path:
    """A stand-in for the claude binary. It records its argv, stdin, working folder and the names (never
    the values) of its environment variables in tmp_path/record, then runs ``body``."""
    record = tmp_path / "record"
    script = tmp_path / "bin" / "claude"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    sys.exit(0)\n"
        f"record = Path({str(record)!r})\n"
        "record.mkdir(parents=True, exist_ok=True)\n"
        "(record / 'argv.json').write_text(json.dumps(sys.argv))\n"
        "(record / 'stdin.txt').write_text(sys.stdin.read(), encoding='utf-8')\n"
        "(record / 'env.json').write_text(json.dumps(sorted(os.environ)))\n"
        "(record / 'cwd.txt').write_text(os.getcwd())\n" + textwrap.dedent(body),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def prints(stdout: dict | str | None, *, stderr: str = "", code: int = 0) -> str:
    """A fake body that writes ``stdout`` (a dict as JSON) and ``stderr`` and exits with ``code``."""
    text = json.dumps(stdout) if isinstance(stdout, dict) else stdout or ""
    return f"sys.stdout.write({text!r})\nsys.stderr.write({stderr!r})\nsys.exit({code})\n"


def recorded(tmp_path: Path, name: str):
    text = (tmp_path / "record" / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


def test_claude_cli_command_and_stdin(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "not-a-real-token")
    monkeypatch.setenv("CLIPFORGE_CANARY", "1")
    binary = fake_claude(tmp_path, prints(SUCCESS))
    workdir = tmp_path / "run" / "llm"  # not there yet
    prompt = "Ça va? " + "word " * 40_000  # more than a pipe holds at once
    response = ClaudeCLIProvider(str(binary), workdir=workdir).complete_json(request(prompt))
    assert response.data == {"answer": "yes", "n": 2}
    assert response.meta == {
        "total_cost_usd": 0.0123,
        "duration_ms": 1500,
        "usage": {"input_tokens": 10, "output_tokens": 4},
        "modelUsage": {"claude-sonnet": {"costUSD": 0.0123}},
    }
    assert recorded(tmp_path, "argv.json") == [
        str(binary), "-p", "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
        "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
        "--model", "sonnet", "--system-prompt", "Be brief.",
    ]  # fmt: skip
    assert recorded(tmp_path, "stdin.txt") == prompt
    names = recorded(tmp_path, "env.json")
    assert "ANTHROPIC_API_KEY" not in names and "ANTHROPIC_AUTH_TOKEN" not in names
    assert "CLIPFORGE_CANARY" in names  # everything else is passed on
    assert Path(recorded(tmp_path, "cwd.txt")).resolve() == workdir.resolve()
    assert os.environ["ANTHROPIC_API_KEY"] == "not-a-real-key"  # the parent's own environment is untouched


def test_claude_cli_keeps_the_key_when_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    binary = fake_claude(tmp_path, prints(SUCCESS))
    ClaudeCLIProvider(str(binary), workdir=tmp_path / "w", strip_api_key=False).complete_json(request())
    assert "ANTHROPIC_API_KEY" in recorded(tmp_path, "env.json")


def test_claude_cli_usage_limit_detected(tmp_path):
    text = "You've hit your session limit, resets 3:30am (Australia/Sydney)"
    binary = fake_claude(tmp_path, prints({"is_error": True, "result": text}, code=1))
    with pytest.raises(LLMError) as caught:
        ClaudeCLIProvider(str(binary), workdir=tmp_path / "w").complete_json(request())
    err = caught.value
    assert err.kind == "usage_limit"
    assert err.message == LLM_MESSAGES["usage_limit"]
    assert text in err.details
    assert time.time() < err.retry_at <= time.time() + 24 * 3600 + 120


def test_claude_cli_not_logged_in(tmp_path):
    binary = fake_claude(
        tmp_path, prints({"is_error": True, "result": "Not logged in · Please run /login"}, code=1)
    )
    with pytest.raises(LLMError) as caught:
        ClaudeCLIProvider(str(binary), workdir=tmp_path / "w").complete_json(request())
    assert caught.value.kind == "auth"
    assert caught.value.message == "Claude Code isn't logged in. Open Terminal, run claude and log in."
    assert caught.value.retry_at is None


@pytest.mark.parametrize(
    ("stdout", "stderr", "code", "kind"),
    [
        (None, "Invalid API key · Please run /login", 1, "auth"),
        ({"is_error": True, "result": "API Error: 401 authentication_error"}, "", 1, "auth"),
        ({"is_error": True, "result": "API Error: 529 Overloaded"}, "", 1, "rate_limit"),
        ({"is_error": True, "result": "API Error: Rate limit reached for requests"}, "", 1, "rate_limit"),
        ({"is_error": True, "result": "Claude AI usage limit reached|1760000000"}, "", 1, "usage_limit"),
        ({"is_error": True, "result": "5-hour limit reached"}, "", 1, "usage_limit"),
        ({"is_error": True, "result": "Usage limit hit"}, "", 1, "usage_limit"),
        ({"is_error": True, "result": "Session limit hit"}, "", 1, "usage_limit"),
        ({"is_error": True, "result": "You've used your weekly limit"}, "", 1, "usage_limit"),
        ({"is_error": True, "result": "You've hit your limit"}, "", 1, "usage_limit"),
        (
            {"is_error": True, "result": "Your limit resets Monday"},
            "",
            1,
            "usage_limit",
        ),  # no clock time: no retry_at
        (
            {"is_error": True, "errors": ["Not logged in"]},
            "",
            1,
            "auth",
        ),  # the words may come as a list of errors
        ({"is_error": True, "result": "Please run /login"}, "", 1, "auth"),
        ({"is_error": True, "result": "Invalid API key"}, "", 1, "auth"),
        ({"is_error": True, "result": "API Error: 529"}, "", 1, "rate_limit"),
        ({"is_error": True, "result": "Server overloaded"}, "", 1, "rate_limit"),
        ({"is_error": True, "result": "rate_limit_error"}, "", 1, "rate_limit"),
        ({"is_error": True, "result": "Rate limit reached, try again in 2 minutes"}, "", 1, "rate_limit"),
        ({"is_error": True, "result": "API Error: 500 on request req_015290"}, "", 1, "other"),
        ({"is_error": True, "result": "Something odd happened"}, "", 1, "other"),
        (None, "", 1, "other"),
        ({"is_error": False, "subtype": "success", "result": "Here you go"}, "", 0, "bad_output"),
        ({"is_error": True, "subtype": "error_max_structured_output_retries"}, "", 1, "bad_output"),
        ({**SUCCESS, "structured_output": ["yes", 2]}, "", 0, "bad_output"),
        ("Done.", "", 0, "bad_output"),
        (SUCCESS, "", 1, "other"),  # a clean answer from a run that failed is not trusted
    ],
)
def test_claude_cli_error_kinds(tmp_path, stdout, stderr, code, kind):
    binary = fake_claude(tmp_path, prints(stdout, stderr=stderr, code=code))
    with pytest.raises(LLMError) as caught:
        ClaudeCLIProvider(str(binary), workdir=tmp_path / "w").complete_json(request())
    assert caught.value.kind == kind
    assert caught.value.retry_at is None


SLEEPS = """\
child = subprocess.Popen(["sleep", "30"])  # holds the output pipes open, as a stuck helper would
(record / "child.pid").write_text(str(child.pid))
time.sleep(30)
"""


def gone(pid: int, within_s: float = 2.0) -> bool:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_claude_cli_timeout_kills_process(tmp_path):
    binary = fake_claude(tmp_path, SLEEPS)
    subprocess.run(
        [str(binary), "--version"], check=True, timeout=30
    )  # macOS checks a new program on its first run
    started = time.monotonic()
    with pytest.raises(LLMError) as caught:
        ClaudeCLIProvider(str(binary), workdir=tmp_path / "w").complete_json(request(timeout_s=0.5))
    assert caught.value.kind == "timeout"
    assert caught.value.message == "Claude took too long to answer."
    assert time.monotonic() - started < 3
    assert gone(int(recorded(tmp_path, "child.pid")))


def test_claude_cli_missing(tmp_path, monkeypatch):
    with pytest.raises(LLMError) as caught:
        ClaudeCLIProvider(str(tmp_path / "nowhere" / "claude"), workdir=tmp_path / "w").complete_json(
            request()
        )
    assert caught.value.kind == "unavailable"
    assert caught.value.message == claude_cli.CLAUDE_MISSING

    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(LLMError) as caught:
        ClaudeCLIProvider(workdir=tmp_path / "w")
    assert (caught.value.kind, caught.value.message) == ("unavailable", claude_cli.CLAUDE_MISSING)

    local = tmp_path / ".local" / "bin" / "claude"  # where Claude Code's installer puts it
    local.parent.mkdir(parents=True)
    local.write_text("")
    assert ClaudeCLIProvider(workdir=tmp_path / "w").binary == str(local)
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/opt/elsewhere/claude")
    assert ClaudeCLIProvider(workdir=tmp_path / "w").binary == "/opt/elsewhere/claude"


# Reset times

SYDNEY = ZoneInfo("Australia/Sydney")


def at(hour: int, minute: int, day: int = 20) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=SYDNEY)


def test_reset_time_parsing():
    text = "You've hit your session limit, resets 3:30am (Australia/Sydney)"
    assert parse_reset_time(text, at(1, 10)) == at(3, 32).timestamp()
    assert parse_reset_time(text, at(4, 0)) == at(3, 32, day=21).timestamp()
    assert (
        parse_reset_time(text, at(3, 35)) == at(3, 37).timestamp()
    )  # just past it: a late reset, ask again soon
    assert parse_reset_time("resets at 3:30 am", at(1, 10)) == at(3, 32).timestamp()
    assert parse_reset_time("Limit reached, resets 11pm", at(1, 10)) == at(23, 2).timestamp()
    assert parse_reset_time("resets 12am", at(1, 10)) == at(0, 2, day=21).timestamp()
    assert parse_reset_time("resets 12:15pm", at(1, 10)) == at(12, 17).timestamp()
    assert parse_reset_time("Your limit will reset at 3pm.", at(1, 10)) == at(15, 2).timestamp()
    assert parse_reset_time("resets 13:30", at(1, 10)) == at(13, 32).timestamp()  # a 24-hour clock
    assert parse_reset_time("resets in 2 hours", at(1, 10)) == at(1, 10).timestamp() + 7200
    assert parse_reset_time("Try again in 45 minutes", at(1, 10)) == at(1, 10).timestamp() + 2700
    assert parse_reset_time("resets in 1 hour 30 minutes", at(1, 10)) == at(1, 10).timestamp() + 5400
    assert parse_reset_time("soon", at(1, 10)) is None


def test_reset_time_zones_and_nonsense():
    utc = datetime(2026, 10, 19, 15, 2, tzinfo=UTC)
    assert parse_reset_time("resets 3pm (UTC)", at(1, 10)) == utc.timestamp()  # 01:10 Sydney is 14:10 UTC
    assert (
        parse_reset_time("resets 3:30am (Mars/Olympus)", at(1, 10)) == at(3, 32).timestamp()
    )  # unknown: local
    assert parse_reset_time("resets 5", at(1, 10)) is None  # a bare number is no clock time
    assert parse_reset_time("resets 13pm", at(1, 10)) is None
    assert parse_reset_time("resets 3:75am", at(1, 10)) is None
