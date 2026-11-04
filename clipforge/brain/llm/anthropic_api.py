"""The Anthropic API on the person's own key (SPEC 9.2). The model is made to call one tool whose input is
the schema, so the answer comes back as that tool call's input."""

from __future__ import annotations

import httpx

from clipforge.brain.llm.base import LLMRequest, LLMResponse, post_json
from clipforge.errors import LLMError

API_VERSION = "2023-06-01"
MAX_TOKENS = 16000


class AnthropicProvider:
    name = "anthropic"
    label = "Anthropic"

    def __init__(
        self, api_key: str, *, base_url: str = "https://api.anthropic.com", client: httpx.Client | None = None
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        body = post_json(
            f"{self.base_url}/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": API_VERSION},
            body={
                "model": req.model,
                "max_tokens": MAX_TOKENS,
                "system": req.system,
                "messages": [{"role": "user", "content": req.prompt}],
                "tools": [
                    {"name": "respond", "description": "Return the answer.", "input_schema": req.schema}
                ],
                "tool_choice": {"type": "tool", "name": "respond"},
            },
            timeout_s=req.timeout_s,
            client=self.client,
            label=self.label,
        )
        content = body.get("content")
        for block in content if isinstance(content, list) else []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and isinstance(block.get("input"), dict)
            ):
                return LLMResponse(block["input"], {"usage": body.get("usage")})
        raise LLMError(
            "bad_output", details=f"no call to the respond tool (stop_reason {body.get('stop_reason')})"
        )
