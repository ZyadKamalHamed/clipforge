"""Which brain a project uses (SPEC 9.2). API keys live in the macOS Keychain under the service ClipForge
(SPEC 17), never in the settings file."""

from __future__ import annotations

import logging
from collections.abc import Callable

import keyring

from clipforge.brain.llm.anthropic_api import AnthropicProvider
from clipforge.brain.llm.base import LLMProvider
from clipforge.brain.llm.claude_cli import ClaudeCLIProvider
from clipforge.brain.llm.gemini import GeminiProvider
from clipforge.brain.llm.openai_compat import OpenAICompatProvider
from clipforge.config import Settings
from clipforge.errors import LLMError
from clipforge.paths import Paths

log = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "ClipForge"

KeyringGet = Callable[[str, str], str | None]


def get_provider(
    settings: Settings,
    name: str | None = None,
    *,
    paths: Paths,
    keyring_get: KeyringGet = keyring.get_password,
) -> LLMProvider | None:
    """The provider called ``name`` (else the one in settings), or None for Basic mode. A missing API key
    raises LLMError(kind="auth") that asks for it."""
    name = name or settings.llm_provider
    if name == "heuristic":
        return None
    if name == "claude_cli":
        return ClaudeCLIProvider(workdir=paths.run_dir / "llm")
    if name == "anthropic":
        return AnthropicProvider(_required_key(keyring_get, "anthropic_api_key", "Anthropic"))
    if name == "gemini":
        return GeminiProvider(_required_key(keyring_get, "gemini_api_key", "Gemini"))
    if name == "openai_compat":
        key = _stored_key(keyring_get, "openai_compat_api_key")
        return OpenAICompatProvider(settings.openai_compat_base_url, api_key=key)
    raise LLMError("other", f"ClipForge doesn't know the AI provider '{name}'.")


def _required_key(keyring_get: KeyringGet, account: str, label: str) -> str:
    key = _stored_key(keyring_get, account)
    if key is None:
        raise LLMError("auth", f"Add your {label} API key in Settings.")
    return key


def _stored_key(keyring_get: KeyringGet, account: str) -> str | None:
    try:
        key = keyring_get(KEYCHAIN_SERVICE, account)
    except Exception as exc:  # a locked or missing keychain reads as no key
        log.warning("Couldn't read %s from the Keychain (%s)", account, type(exc).__name__)
        return None
    if not isinstance(key, str) or not key.strip():
        return None
    return key.strip()
