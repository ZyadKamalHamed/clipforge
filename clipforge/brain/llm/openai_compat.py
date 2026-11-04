"""Any OpenAI-compatible server (SPEC 9.2): Ollama, LM Studio, llama.cpp or a hosted one, asked for JSON
that fits the schema. Local models often think out loud or fence their JSON, so the answer is dug out of
the text."""

from __future__ import annotations

import json

import httpx

from clipforge.brain.llm.base import DETAILS_CHARS, LLMRequest, LLMResponse, parse_json_text, post_json
from clipforge.errors import LLMError


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str, *, api_key: str | None = None, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = client
        self.label = f"The AI server at {self.base_url}"

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        headers = (
            {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        )  # a local one needs none
        body = post_json(
            f"{self.base_url}/chat/completions",
            headers=headers,
            body={
                "model": req.model,
                "messages": [
                    {"role": "system", "content": req.system},
                    {"role": "user", "content": req.prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": req.schema},
                },
                "temperature": 0,
            },
            timeout_s=req.timeout_s,
            client=self.client,
            label=self.label,
        )
        content = _message_content(body)
        if content is None:  # no choices, or a refusal instead of an answer
            raise LLMError("bad_output", details=json.dumps(body, ensure_ascii=False)[:DETAILS_CHARS])
        return LLMResponse(parse_json_text(content), {"usage": body.get("usage")})


def _message_content(body: dict) -> str | None:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else None
