"""One structured call to any provider (SPEC 9.2): a JSON schema, a timeout, one retry, a cached answer
and a process-wide cap on how many calls run at once. A usage limit or a login problem is never retried:
the project waits or switches to Basic mode instead."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from clipforge.brain.llm.cache import LLMCache, cache_key
from clipforge.errors import LLMError

log = logging.getLogger(__name__)

FORMAT_NOTE = (
    "Your previous answer did not match the required format: {errors}. Answer again in the required format."
)
NOT_RETRIED = frozenset({"usage_limit", "auth"})
RATE_LIMIT_PAUSE_S = 5.0  # a busy service gets a moment before the one retry
DETAILS_CHARS = 500


@dataclass(frozen=True)
class LLMRequest:
    pass_name: str
    system: str
    prompt: str
    schema: dict
    model: str
    timeout_s: float = 180.0


@dataclass
class LLMResponse:
    data: dict
    meta: dict


class LLMProvider(Protocol):
    name: str

    def complete_json(self, req: LLMRequest) -> LLMResponse: ...


_slots = threading.BoundedSemaphore(3)


def set_parallel(n: int) -> None:
    """How many LLM calls may run at once in this process (at least one)."""
    global _slots
    _slots = threading.BoundedSemaphore(max(1, int(n)))


@contextmanager
def llm_slot() -> Iterator[None]:
    slots = _slots  # released to the one it came from, even if set_parallel swaps it meanwhile
    with slots:
        yield


def call_structured[T: BaseModel](
    provider: LLMProvider,
    req: LLMRequest,
    out_model: type[T],
    *,
    cache: LLMCache | None = None,
    log_dir: Path | None = None,
    retries: int = 1,
) -> T:
    """The provider's answer to ``req`` as ``out_model``. A cached answer is used when it still fits. A
    failed call is asked again up to ``retries`` times; an answer that doesn't fit the model is asked again
    with its errors. The answer is cached under the original question."""
    key = cache_key(provider.name, req.model, req.system, req.prompt, req.schema)
    if cache is not None and (hit := cache.get(key)) is not None:
        try:
            return out_model.model_validate(hit)
        except ValidationError:
            log.info("%s: the cached answer %s no longer fits, asking again", req.pass_name, key[:10])
    ask = req
    for attempt in range(retries + 1):
        stem = f"{req.pass_name}-{key[:10]}" + (f".retry{attempt}" if attempt else "")
        if log_dir is not None:
            _write_prompt(log_dir, stem, ask)
        try:
            with llm_slot():
                started = time.monotonic()
                response = provider.complete_json(ask)
        except LLMError as err:
            if log_dir is not None:
                error = {"kind": err.kind, "message": err.message, "details": err.details}
                _write_json(log_dir, stem, {"error": error})
            if err.kind in NOT_RETRIED or attempt == retries:
                raise
            log.warning("%s: %s failed (%s), asking again", req.pass_name, provider.name, err.kind)
            if err.kind == "rate_limit":
                time.sleep(RATE_LIMIT_PAUSE_S)
            continue  # with the same question, format note and all
        if log_dir is not None:
            _write_json(log_dir, stem, {"data": response.data, "meta": response.meta})
        try:
            answer = out_model.model_validate(response.data)
        except ValidationError as err:
            errors = _first_errors(err)
            if attempt == retries:
                raise LLMError("bad_output", details=errors) from err
            log.warning("%s: the answer did not fit (%s), asking again", req.pass_name, errors)
            ask = replace(req, prompt=f"{req.prompt}\n\n{FORMAT_NOTE.format(errors=errors)}")
            continue
        cost = response.meta.get("total_cost_usd")
        log.info(
            "%s: %s %s answered in %.1f s%s",
            req.pass_name,
            provider.name,
            req.model,
            time.monotonic() - started,
            f" (${cost:.4f})" if isinstance(cost, int | float) else "",
        )
        if cache is not None:
            cache.put(key, response.data)
        return answer
    raise AssertionError("unreachable")  # every attempt returns, raises or continues


def _first_errors(err: ValidationError, n: int = 3) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc']) or 'the answer'}: {e['msg']}" for e in err.errors()[:n]
    )


def _write_prompt(log_dir: Path, stem: str, req: LLMRequest) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    text = f"model: {req.model}\n\n[system]\n{req.system}\n\n[prompt]\n{req.prompt}\n"
    (log_dir / f"{stem}.prompt.txt").write_text(text, encoding="utf-8")


def _write_json(log_dir: Path, stem: str, payload: dict) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


# Shared by the HTTP providers


def post_json(
    url: str,
    *,
    headers: dict[str, str],
    body: dict,
    timeout_s: float,
    client: httpx.Client | None,
    label: str,
) -> dict:
    """POST ``body`` and return the JSON object that comes back. Failures raise LLMError with plain
    messages that name the service as ``label``."""
    try:
        if client is None:
            with httpx.Client() as own:
                response = own.post(url, headers=headers, json=body, timeout=timeout_s)
        else:
            response = client.post(url, headers=headers, json=body, timeout=timeout_s)
    except httpx.TimeoutException as err:
        raise LLMError("timeout", f"{label} took too long to answer.", details=str(err)) from err
    except httpx.HTTPError as err:
        raise LLMError("unavailable", f"{label} can't be reached right now.", details=str(err)) from err
    status = response.status_code
    details = f"HTTP {status}: {response.text[:DETAILS_CHARS]}"
    if status in (401, 403):
        raise LLMError("auth", f"{label} didn't accept the API key. Check it in Settings.", details=details)
    if status == 429:
        raise LLMError("rate_limit", f"{label} is busy right now.", details=details)
    if status >= 500:
        raise LLMError("unavailable", f"{label} can't be reached right now.", details=details)
    if status >= 300:
        raise LLMError("other", f"{label} refused the request.", details=details)
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise LLMError("bad_output", details=details)
    return data


THINKING = re.compile(r"<think>.*?</think>", re.S)


def parse_json_text(text: str) -> dict:
    """The JSON object in a model's written answer, past any <think> block, code fence or chatter."""
    text = THINKING.sub("", text).strip()
    for chunk in (text, text[text.find("{") : text.rfind("}") + 1]):
        try:
            data = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise LLMError("bad_output", details=text[:DETAILS_CHARS])
