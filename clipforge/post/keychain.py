"""Account tokens in the macOS Keychain, under the same service as the API keys. The database only ever
holds the item's name."""

from __future__ import annotations

import contextlib

import keyring
from keyring.errors import PasswordDeleteError

from clipforge.brain.llm.registry import KEYCHAIN_SERVICE

SERVICE = KEYCHAIN_SERVICE


def ref_for(account_id: str) -> str:
    return f"account:{account_id}"


def put_token(ref: str, token: str) -> None:
    keyring.set_password(SERVICE, ref, token)


def get_token(ref: str) -> str | None:
    return keyring.get_password(SERVICE, ref)


def delete_token(ref: str) -> None:
    with contextlib.suppress(PasswordDeleteError):  # already gone
        keyring.delete_password(SERVICE, ref)
