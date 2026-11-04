"""The real Claude Code CLI with this Mac's own login. Run on purpose with:
uv run pytest -m live tests/live/test_claude_live.py -q

It asks Haiku one tiny question, so it uses a sliver of the plan's usage. It needs claude installed and
logged in."""

import json

import pytest
from pydantic import BaseModel

from clipforge.brain.llm.base import LLMRequest, call_structured
from clipforge.brain.llm.cache import cache_key
from clipforge.brain.llm.claude_cli import META_KEYS, ClaudeCLIProvider
from clipforge.brain.llm.schema import schema_for

pytestmark = pytest.mark.live


class Pair(BaseModel):
    word: str
    number: int


def test_claude_cli_live_haiku(tmp_path):
    provider = ClaudeCLIProvider(workdir=tmp_path / "run" / "llm")
    req = LLMRequest(
        pass_name="live",
        system="You answer in the required format and nothing else.",
        prompt="Give the word clip and the number 7.",
        schema=schema_for(Pair),
        model="haiku",
        timeout_s=120.0,
    )
    answer = call_structured(provider, req, Pair, log_dir=tmp_path / "log")
    assert answer.word.strip().lower() == "clip"
    assert answer.number == 7
    key = cache_key(provider.name, req.model, req.system, req.prompt, req.schema)
    logged = json.loads((tmp_path / "log" / f"live-{key[:10]}.json").read_text())
    assert set(logged["meta"]) <= set(META_KEYS)
    assert logged["meta"]["total_cost_usd"] >= 0
