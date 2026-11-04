"""The Gemini API on the person's own key (SPEC 9.2), in JSON mode with the schema."""

from __future__ import annotations

import json

import httpx

from clipforge.brain.llm.base import DETAILS_CHARS, LLMRequest, LLMResponse, parse_json_text, post_json
from clipforge.errors import LLMError


class GeminiProvider:
    name = "gemini"
    label = "Gemini"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        body = post_json(
            f"{self.base_url}/models/{req.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            body={
                "systemInstruction": {"parts": [{"text": req.system}]},
                "contents": [{"role": "user", "parts": [{"text": req.prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": req.schema,
                    "temperature": 0,
                },
            },
            timeout_s=req.timeout_s,
            client=self.client,
            label=self.label,
        )
        text = _answer_text(body)
        if text is None:  # no candidates, a blocked prompt, or thoughts only
            raise LLMError("bad_output", details=json.dumps(body, ensure_ascii=False)[:DETAILS_CHARS])
        return LLMResponse(parse_json_text(text), {"usage": body.get("usageMetadata")})


def _answer_text(body: dict) -> str | None:
    """The first candidate's written answer, past any thought summaries."""
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return None
    content = candidates[0].get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    for part in parts if isinstance(parts, list) else []:
        if isinstance(part, dict) and not part.get("thought") and isinstance(part.get("text"), str):
            return part["text"]
    return None
