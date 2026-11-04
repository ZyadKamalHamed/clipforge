import json
import re
import threading
import time
from typing import Literal

import pytest
from pydantic import BaseModel, Field

from clipforge.brain.llm import base
from clipforge.brain.llm.base import LLMRequest, LLMResponse, call_structured, llm_slot, set_parallel
from clipforge.brain.llm.cache import LLMCache, cache_key
from clipforge.brain.llm.schema import schema_for
from clipforge.errors import LLM_MESSAGES, LLMError

STRIPPED = {
    "title", "default", "examples", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "format",
    "uniqueItems", "multipleOf", "minProperties", "maxProperties",
}  # fmt: skip


@pytest.fixture(autouse=True)
def default_parallel():
    yield
    set_parallel(3)


class Inner(BaseModel):
    title: str  # a field may be called like a keyword the cleaner drops
    score: int = Field(ge=0, le=10)


class Outer(BaseModel):
    """The answer."""

    kind: Literal["hook", "story"]
    count: int | None = None
    label: str = Field(max_length=42, description="A short label.", examples=["a"])
    sid: str = Field(pattern=r"^S\d+$")
    inner: Inner = Field(description="The inner one.")
    maybe: Inner | None = None
    items: list[Inner] = Field(min_length=1, max_length=5)
    extra: dict[str, int] = {}
    tags: set[str] = set()
    step: int = Field(default=5, multiple_of=5, gt=0, lt=100)


def schema_nodes(node):
    """Every schema inside ``node``, itself included; property names and enum values are not schemas."""
    if isinstance(node, list):
        for item in node:
            yield from schema_nodes(item)
    elif isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key == "properties":
                for sub in value.values():
                    yield from schema_nodes(sub)
            elif key not in ("enum", "const", "required"):
                yield from schema_nodes(value)


INNER = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "score": {"type": "integer"}},
    "required": ["title", "score"],
    "additionalProperties": False,
}


def test_schema_for_strips_constraints():
    schema = schema_for(Outer)
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    nodes = list(schema_nodes(schema))
    assert not STRIPPED & {key for node in nodes for key in node}
    for node in nodes:
        if "properties" in node:
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])
    props = schema["properties"]
    assert schema["description"] == "The answer."
    assert schema["required"] == list(Outer.model_fields)  # every key, even the ones with defaults
    assert props["kind"] == {"enum": ["hook", "story"], "type": "string"}
    assert props["count"] == {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert props["label"] == {"description": "A short label.", "type": "string"}
    assert props["sid"] == {"type": "string"}
    assert props["inner"] == {**INNER, "description": "The inner one."}
    assert props["maybe"] == {"anyOf": [INNER, {"type": "null"}]}
    assert props["items"] == {"type": "array", "items": INNER}
    assert props["extra"] == {
        "type": "object",
        "additionalProperties": {"type": "integer"},
    }  # a dict stays open
    assert props["tags"] == {"type": "array", "items": {"type": "string"}}
    assert props["step"] == {"type": "integer"}
    assert schema_for(Outer) == schema  # the model's own schema isn't changed along the way


# The cache


def test_cache_key_covers_all_five():
    args = ["claude_cli", "opus", "system", "prompt", {"type": "object", "properties": {}}]
    key = cache_key(*args)
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert cache_key(*args) == key
    for k in range(5):
        changed = list(args)
        changed[k] = {"type": "array"} if k == 4 else args[k] + "!"
        assert cache_key(*changed) != key
    assert cache_key("claude_cli", "opus", "system", "prompt", {"properties": {}, "type": "object"}) == key


def test_cache_put_then_get(tmp_path):
    cache = LLMCache(tmp_path / "cache")
    key = "a" * 64
    assert cache.get(key) is None
    cache.put(key, {"x": 1})
    cache.put(key, {"x": 2})
    assert cache.get(key) == {"x": 2}
    assert [p.name for p in (tmp_path / "cache").iterdir()] == [f"{key}.json"]  # no temporary file left
    (tmp_path / "cache" / f"{key}.json").write_text('{"x": ')  # cut short
    assert cache.get(key) is None
    (tmp_path / "cache" / f"{key}.json").write_text("[1, 2]")
    assert cache.get(key) is None


# Calls


class Answer(BaseModel):
    kind: Literal["hook", "story"]
    n: int


GOOD = {"kind": "hook", "n": 2}
REQ = LLMRequest(
    pass_name="discover",
    system="Pick moments.",
    prompt="The transcript.",
    schema=schema_for(Answer),
    model="opus",
    timeout_s=60.0,
)


class FakeProvider:
    """Answers with each of ``answers`` in turn (an exception is raised) and records every request."""

    name = "fake"

    def __init__(self, *answers, delay_s: float = 0.0):
        self.answers = list(answers)
        self.requests: list[LLMRequest] = []
        self.delay_s = delay_s

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        time.sleep(self.delay_s)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return LLMResponse(data=answer, meta={"total_cost_usd": 0.01})


def key_of(req: LLMRequest) -> str:
    return cache_key("fake", req.model, req.system, req.prompt, req.schema)


def test_cache_hit_skips_provider(tmp_path):
    cache = LLMCache(tmp_path / "cache")
    first = call_structured(FakeProvider(GOOD), REQ, Answer, cache=cache)
    assert first == Answer(**GOOD)
    assert json.loads((tmp_path / "cache" / f"{key_of(REQ)}.json").read_text()) == GOOD
    again = FakeProvider()
    assert call_structured(again, REQ, Answer, cache=cache) == first
    assert again.requests == []


def test_a_cached_answer_that_no_longer_fits_is_asked_again(tmp_path):
    cache = LLMCache(tmp_path / "cache")
    cache.put(key_of(REQ), {"kind": "joke", "n": 2})
    provider = FakeProvider(GOOD)
    assert call_structured(provider, REQ, Answer, cache=cache) == Answer(**GOOD)
    assert len(provider.requests) == 1
    assert cache.get(key_of(REQ)) == GOOD


def test_validation_retry_then_success(tmp_path):
    provider = FakeProvider({"kind": "joke", "n": 2}, GOOD)
    cache = LLMCache(tmp_path / "cache")
    answer = call_structured(provider, REQ, Answer, cache=cache)
    assert answer == Answer(**GOOD)
    first, second = provider.requests
    assert first == REQ
    assert second.prompt.startswith(REQ.prompt + "\n\n")
    assert "Your previous answer did not match the required format: kind: " in second.prompt
    assert second.prompt.endswith(". Answer again in the required format.")
    assert (second.system, second.schema, second.model, second.timeout_s) == (
        REQ.system,
        REQ.schema,
        REQ.model,
        REQ.timeout_s,
    )
    # kept under the first request, so the same question later costs nothing
    assert call_structured(FakeProvider(), REQ, Answer, cache=cache) == answer


def test_the_format_note_names_at_most_three_errors():
    class Many(BaseModel):
        a: int
        b: int
        c: int
        d: int

    req = LLMRequest("discover", "s", "p", schema_for(Many), "opus")
    provider = FakeProvider({}, {"a": 1, "b": 2, "c": 3, "d": 4})
    call_structured(provider, req, Many)
    note = provider.requests[1].prompt
    assert "a: Field required; b: Field required; c: Field required. " in note
    assert "d: " not in note


def test_the_format_note_stays_through_a_failed_call():
    provider = FakeProvider({"kind": "joke", "n": 2}, LLMError("timeout"), GOOD)
    assert call_structured(provider, REQ, Answer, retries=2) == Answer(**GOOD)
    assert [req.prompt == REQ.prompt for req in provider.requests] == [True, False, False]


def test_validation_twice_raises_bad_output(tmp_path):
    provider = FakeProvider({"kind": "joke", "n": 2}, {"kind": "hook"})
    with pytest.raises(LLMError) as caught:
        call_structured(provider, REQ, Answer, cache=LLMCache(tmp_path / "cache"))
    assert caught.value.kind == "bad_output"
    assert caught.value.message == LLM_MESSAGES["bad_output"]
    assert "n: Field required" in caught.value.details  # the last answer's errors
    assert len(provider.requests) == 2
    assert not list((tmp_path / "cache").glob("*.json"))


def test_retries_zero_asks_once():
    provider = FakeProvider({"kind": "joke", "n": 2})
    with pytest.raises(LLMError) as caught:
        call_structured(provider, REQ, Answer, retries=0)
    assert caught.value.kind == "bad_output"
    assert len(provider.requests) == 1


@pytest.mark.parametrize("kind", ["timeout", "rate_limit", "unavailable", "bad_output", "other"])
def test_a_failed_call_is_asked_again_once(kind, monkeypatch):
    monkeypatch.setattr(base, "RATE_LIMIT_PAUSE_S", 0.3)
    provider = FakeProvider(LLMError(kind), GOOD)
    started = time.monotonic()
    assert call_structured(provider, REQ, Answer) == Answer(**GOOD)
    assert provider.requests == [REQ, REQ]  # the same question, no format note
    assert (time.monotonic() - started >= 0.3) == (kind == "rate_limit")  # a busy service gets a pause


def test_two_failed_calls_raise_the_last_error(monkeypatch):
    monkeypatch.setattr(base, "RATE_LIMIT_PAUSE_S", 0.0)
    provider = FakeProvider(LLMError("rate_limit"), LLMError("timeout", details="still nothing"))
    with pytest.raises(LLMError) as caught:
        call_structured(provider, REQ, Answer)
    assert (caught.value.kind, caught.value.details) == ("timeout", "still nothing")
    assert len(provider.requests) == 2


@pytest.mark.parametrize("kind", ["usage_limit", "auth"])
def test_limits_and_logins_are_not_asked_again(kind):
    provider = FakeProvider(LLMError(kind, retry_at=123.0), GOOD)
    with pytest.raises(LLMError) as caught:
        call_structured(provider, REQ, Answer)
    assert (caught.value.kind, caught.value.retry_at) == (kind, 123.0)
    assert len(provider.requests) == 1


def test_log_dir_keeps_every_prompt_and_raw_answer(tmp_path):
    provider = FakeProvider(LLMError("timeout", details="no answer in 60 s"), {"kind": "joke", "n": 2}, GOOD)
    call_structured(provider, REQ, Answer, log_dir=tmp_path / "llm", retries=2)
    stem = f"discover-{key_of(REQ)[:10]}"
    files = sorted(p.name for p in (tmp_path / "llm").iterdir())
    assert files == sorted(
        f"{stem}{suffix}{ext}" for suffix in ("", ".retry1", ".retry2") for ext in (".prompt.txt", ".json")
    )
    first = (tmp_path / "llm" / f"{stem}.prompt.txt").read_text()
    assert "opus" in first and REQ.system in first and REQ.prompt in first
    error = json.loads((tmp_path / "llm" / f"{stem}.json").read_text())["error"]
    assert error == {"kind": "timeout", "message": LLM_MESSAGES["timeout"], "details": "no answer in 60 s"}
    assert json.loads((tmp_path / "llm" / f"{stem}.retry1.json").read_text()) == {
        "data": {"kind": "joke", "n": 2},
        "meta": {"total_cost_usd": 0.01},
    }
    third = (tmp_path / "llm" / f"{stem}.retry2.prompt.txt").read_text()
    assert "did not match the required format" in third
    assert json.loads((tmp_path / "llm" / f"{stem}.retry2.json").read_text())["data"] == GOOD


# How many calls run at once


def test_llm_slot_limits_parallel_calls():
    set_parallel(2)
    lock, running, peak = threading.Lock(), [0], [0]

    def work():
        with llm_slot():
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.05)
            with lock:
                running[0] -= 1

    threads = [threading.Thread(target=work, daemon=True) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert peak[0] == 2


def test_calls_take_a_slot():
    set_parallel(1)
    provider = FakeProvider(GOOD, GOOD, GOOD, delay_s=0.05)
    lock, inside, peak = threading.Lock(), [0], [0]
    real = provider.complete_json

    def counted(req):
        with lock:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
        try:
            return real(req)
        finally:
            with lock:
                inside[0] -= 1

    provider.complete_json = counted
    threads = [
        threading.Thread(target=call_structured, args=(provider, REQ, Answer), daemon=True) for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len(provider.requests) == 3
    assert peak[0] == 1


def test_set_parallel_never_goes_below_one():
    set_parallel(0)

    def take():
        with llm_slot():
            pass

    thread = threading.Thread(target=take, daemon=True)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
