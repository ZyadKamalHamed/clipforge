import json

import httpx
import pytest
from keyring.errors import KeyringLocked

from clipforge.brain.llm import claude_cli
from clipforge.brain.llm.anthropic_api import AnthropicProvider
from clipforge.brain.llm.base import LLMRequest
from clipforge.brain.llm.claude_cli import ClaudeCLIProvider
from clipforge.brain.llm.gemini import GeminiProvider
from clipforge.brain.llm.openai_compat import OpenAICompatProvider
from clipforge.brain.llm.registry import get_provider
from clipforge.config import Settings
from clipforge.errors import LLMError
from clipforge.paths import Paths

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "n": {"type": "integer"}},
    "required": ["answer", "n"],
    "additionalProperties": False,
}
ANSWER = {"answer": "yes", "n": 2}


def request(model: str = "some-model") -> LLMRequest:
    return LLMRequest("discover", "Be brief.", "Pick one.", SCHEMA, model, 42.0)


def mock_client(status: int = 200, body=None, *, text: str | None = None, error: type | None = None):
    """An httpx client whose every request gets this answer, and the list of requests it saw."""
    seen: list[httpx.Request] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if error is not None:
            raise error("no luck", request=req)
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(answer)), seen


def test_anthropic_tool_use_parsing():
    client, seen = mock_client(
        body={
            "content": [
                {"type": "text", "text": "Sure."},
                {"type": "tool_use", "id": "toolu_1", "name": "respond", "input": ANSWER},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 5},
            "stop_reason": "tool_use",
        }
    )
    response = AnthropicProvider("test-key", client=client).complete_json(request("claude-sonnet-5-5"))
    assert response.data == ANSWER
    assert response.meta == {"usage": {"input_tokens": 12, "output_tokens": 5}}
    sent = seen[0]
    assert (sent.method, str(sent.url)) == ("POST", "https://api.anthropic.com/v1/messages")
    assert sent.headers["x-api-key"] == "test-key"
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert json.loads(sent.content) == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 16000,
        "system": "Be brief.",
        "messages": [{"role": "user", "content": "Pick one."}],
        "tools": [{"name": "respond", "description": "Return the answer.", "input_schema": SCHEMA}],
        "tool_choice": {"type": "tool", "name": "respond"},
    }
    assert sent.extensions["timeout"]["read"] == 42.0


def test_gemini_parsing():
    client, seen = mock_client(
        body={
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Weighing it up.", "thought": True}, {"text": json.dumps(ANSWER)}],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 9},
        }
    )
    response = GeminiProvider("test-key", client=client).complete_json(request("gemini-flash-latest"))
    assert response.data == ANSWER
    assert response.meta == {"usage": {"promptTokenCount": 9}}
    sent = seen[0]
    assert str(sent.url) == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    )
    assert sent.headers["x-goog-api-key"] == "test-key"
    assert json.loads(sent.content) == {
        "systemInstruction": {"parts": [{"text": "Be brief."}]},
        "contents": [{"role": "user", "parts": [{"text": "Pick one."}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": SCHEMA,
            "temperature": 0,
        },
    }
    assert sent.extensions["timeout"]["read"] == 42.0


def test_openai_compat_parsing():
    content = '<think>Maybe {"answer": "no"}?</think>\n```json\n' + json.dumps(ANSWER) + "\n```"
    body = {
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 9},
    }
    client, seen = mock_client(body=body)
    keyed = OpenAICompatProvider("http://127.0.0.1:11434/v1/", api_key="test-key", client=client)
    response = keyed.complete_json(request("qwen3:14b"))
    assert response.data == ANSWER
    assert response.meta == {"usage": {"prompt_tokens": 9}}
    sent = seen[0]
    assert str(sent.url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer test-key"
    assert json.loads(sent.content) == {
        "model": "qwen3:14b",
        "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Pick one."}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "response", "schema": SCHEMA}},
        "temperature": 0,
    }
    assert sent.extensions["timeout"]["read"] == 42.0
    OpenAICompatProvider("http://127.0.0.1:11434/v1", client=client).complete_json(request())
    assert "authorization" not in seen[1].headers  # a local server needs no key


PROVIDERS = {
    "anthropic": lambda client: AnthropicProvider("test-key", client=client),
    "gemini": lambda client: GeminiProvider("test-key", client=client),
    "openai_compat": lambda client: OpenAICompatProvider("http://127.0.0.1:11434/v1", client=client),
}
LABELS = {"anthropic": "Anthropic", "gemini": "Gemini", "openai_compat": "http://127.0.0.1:11434/v1"}


def failure(name: str, client: httpx.Client) -> LLMError:
    with pytest.raises(LLMError) as caught:
        PROVIDERS[name](client).complete_json(request())
    return caught.value


@pytest.mark.parametrize("name", sorted(PROVIDERS))
@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "rate_limit"),
        (500, "unavailable"),
        (503, "unavailable"),
        (529, "unavailable"),
        (302, "other"),  # a redirect is not followed: a POST must not turn into something else
        (400, "other"),
        (404, "other"),
    ],
)
def test_http_errors_mapped(name, status, kind):
    err = failure(name, mock_client(status, {"error": {"message": "nope"}})[0])
    assert err.kind == kind
    assert LABELS[name] in err.message  # the message names the service, not Claude Code
    assert "Claude Code" not in err.message
    assert f"HTTP {status}" in err.details and "nope" in err.details


@pytest.mark.parametrize("name", sorted(PROVIDERS))
@pytest.mark.parametrize(
    ("error", "kind"),
    [(httpx.ReadTimeout, "timeout"), (httpx.ConnectTimeout, "timeout"), (httpx.ConnectError, "unavailable")],
)
def test_transport_errors_mapped(name, error, kind):
    err = failure(name, mock_client(error=error)[0])
    assert err.kind == kind
    assert LABELS[name] in err.message


def test_auth_message_points_at_settings():
    err = failure("gemini", mock_client(401, {})[0])
    assert err.message == "Gemini didn't accept the API key. Check it in Settings."


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("anthropic", {"content": []}),
        ("anthropic", {"content": [{"type": "text", "text": "No tool for me."}], "stop_reason": "end_turn"}),
        ("gemini", {"candidates": []}),
        ("gemini", {"promptFeedback": {"blockReason": "SAFETY"}}),
        ("gemini", {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}),
        ("gemini", {"candidates": [{"content": {"parts": [{"text": "[1, 2]"}]}}]}),
        ("openai_compat", {"choices": []}),
        (
            "openai_compat",
            {"choices": [{"message": {"content": None, "refusal": "I can't help with that."}}]},
        ),
        ("openai_compat", {"choices": [{"message": {"content": "Sure! {broken"}}]}),
        ("openai_compat", [1, 2]),
    ],
)
def test_unreadable_answers_are_bad_output(name, body):
    assert failure(name, mock_client(body=body)[0]).kind == "bad_output"


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_a_page_that_is_not_json_is_bad_output(name):
    assert failure(name, mock_client(text="<html>Gateway</html>")[0]).kind == "bad_output"


# Choosing the provider


def keys(**stored: str):
    """A stand-in for keyring.get_password holding ``stored`` under the ClipForge service."""

    def get(service: str, account: str) -> str | None:
        assert service == "ClipForge"
        return stored.get(account)

    return get


def test_get_provider_builds_the_chosen_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_cli, "find_claude", lambda: "/opt/fake/claude")
    paths = Paths(tmp_path / "home")
    cli = get_provider(Settings(), paths=paths, keyring_get=keys())
    assert isinstance(cli, ClaudeCLIProvider)
    assert (cli.name, cli.binary, cli.workdir) == ("claude_cli", "/opt/fake/claude", paths.run_dir / "llm")
    anthropic = get_provider(
        Settings(), "anthropic", paths=paths, keyring_get=keys(anthropic_api_key="test-a\n")
    )
    assert isinstance(anthropic, AnthropicProvider)
    assert (anthropic.name, anthropic.api_key) == ("anthropic", "test-a")  # pasted with a newline
    gemini = get_provider(
        Settings(llm_provider="gemini"), paths=paths, keyring_get=keys(gemini_api_key="test-g")
    )
    assert isinstance(gemini, GeminiProvider)
    assert (gemini.name, gemini.api_key) == ("gemini", "test-g")
    local = get_provider(
        Settings(openai_compat_base_url="http://10.0.0.5:8080/v1"),
        "openai_compat",
        paths=paths,
        keyring_get=keys(),
    )
    assert isinstance(local, OpenAICompatProvider)
    assert (local.name, local.base_url, local.api_key) == ("openai_compat", "http://10.0.0.5:8080/v1", None)
    keyed = get_provider(
        Settings(), "openai_compat", paths=paths, keyring_get=keys(openai_compat_api_key="test-o")
    )
    assert keyed.api_key == "test-o"
    assert get_provider(Settings(), "heuristic", paths=paths, keyring_get=keys()) is None
    assert get_provider(Settings(llm_provider="heuristic"), paths=paths, keyring_get=keys()) is None


@pytest.mark.parametrize(("name", "label"), [("anthropic", "Anthropic"), ("gemini", "Gemini")])
@pytest.mark.parametrize("stored", [None, "  "])
def test_a_missing_key_asks_for_it(tmp_path, name, label, stored):
    with pytest.raises(LLMError) as caught:
        get_provider(Settings(), name, paths=Paths(tmp_path), keyring_get=keys(**{f"{name}_api_key": stored}))
    assert (caught.value.kind, caught.value.message) == ("auth", f"Add your {label} API key in Settings.")


def test_a_locked_keychain_reads_as_no_key(tmp_path):
    def locked(service: str, account: str) -> str | None:
        raise KeyringLocked("locked")

    with pytest.raises(LLMError) as caught:
        get_provider(Settings(), "anthropic", paths=Paths(tmp_path), keyring_get=locked)
    assert caught.value.kind == "auth"
    local = get_provider(Settings(), "openai_compat", paths=Paths(tmp_path), keyring_get=locked)
    assert local.api_key is None


def test_an_unknown_provider_is_an_llm_error(tmp_path):
    with pytest.raises(LLMError) as caught:
        get_provider(Settings(), "mystery", paths=Paths(tmp_path), keyring_get=keys())
    assert caught.value.kind == "other"
    assert "mystery" in caught.value.message
