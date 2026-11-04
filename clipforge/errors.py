"""Errors that carry a plain message for the person using ClipForge (SPEC 21).

``str(err)`` is always the plain message; technical detail goes in ``details`` and the log.
"""

from __future__ import annotations

INTERNAL = "Something went wrong. The log has the details."  # SPEC 18: the sentence for a bug


class ClipForgeError(Exception):
    def __init__(self, code: str, message: str, *, details: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable

    def __str__(self) -> str:
        return self.message


class SourceRefused(ClipForgeError):
    """The source can't be clipped as asked (too long, private, still live, members only)."""


class YouTubeRateLimited(ClipForgeError):
    """YouTube asked us to slow down."""


class NetworkError(ClipForgeError):
    def __init__(self, code: str, message: str, *, details: str | None = None, retryable: bool = True):
        super().__init__(code, message, details=details, retryable=retryable)


class ToolMissing(ClipForgeError):
    """A program ClipForge needs (ffmpeg, Claude Code) is not installed."""


class ToolError(ClipForgeError):
    """A program ClipForge runs failed or took too long."""


class PipelineError(ClipForgeError):
    """A stage can't run because something it needs is missing."""


LLM_MESSAGES = {
    "timeout": "Claude took too long to answer.",
    "auth": "Claude Code isn't logged in. Open Terminal, run claude and log in.",
    "usage_limit": "Claude's usage limit is reached.",
    "rate_limit": "The AI service is busy right now.",
    "bad_output": "The AI's answer could not be read.",
    "unavailable": "The AI service can't be reached.",
    "other": "The AI request failed.",
}


class LLMError(ClipForgeError):
    def __init__(
        self,
        kind: str,
        message: str | None = None,
        *,
        retry_at: float | None = None,
        details: str | None = None,
    ):
        super().__init__(
            f"llm_{kind}", message or LLM_MESSAGES.get(kind, LLM_MESSAGES["other"]), details=details
        )
        self.kind = kind
        self.retry_at = retry_at


class WaitUntil(ClipForgeError):
    """Not a failure: the job should run again at ``until`` (epoch seconds) without using an attempt."""

    def __init__(self, until: float, message: str, *, code: str = "waiting"):
        super().__init__(code, message)
        self.until = until
