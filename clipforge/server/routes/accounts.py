"""The accounts API (SPEC 11, 14.7 and 16): the accounts as their cards show them, adding one (a Chrome
login, or a key pasted for Meta's API), linking the Instagram accounts on Facebook Pages, a new key, Log in,
Check and the self-test, edits checked as they are saved, and removing one. No token, Keychain item, Chrome
profile or Meta id is ever sent back."""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.exceptions import HTTPException

from clipforge import repo
from clipforge.assets import FONTS
from clipforge.config import WINDOW_WRONG, Window
from clipforge.errors import ClipForgeError
from clipforge.post import accounts, instagram_api, queue, store
from clipforge.post.browser.login import INSTAGRAM_HANDLE, NO_TYPED_HANDLE
from clipforge.post.distribute import clean_hashtags
from clipforge.post.errors import Fatal
from clipforge.post.models import Account, AccountSettings
from clipforge.render.styles import CHANNEL, COLOUR, CREDIT_MAX, StyleKit
from clipforge.server.errors import ApiError, Refusal
from clipforge.server.views import account_json, cleaned
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine

MAX_WINDOWS = 8
TIKTOK_API = "TikTok accounts post through Chrome. Choose Log in with Chrome."
NO_KEY_FOR_BROWSER = "An account that logs in with Chrome doesn't take a key."
NO_TOKEN = "Paste the account's access token."
HANDLE_FROM_LOGIN = "This account's handle comes from its login, so it can't be typed."
HANDLE_TAKEN = "That account is already in ClipForge."
TOO_MANY_WINDOWS = f"Give at most {MAX_WINDOWS} posting windows."
PAIR_WRONG = "Pair an account with one of yours on the other platform."
HALF_APP = "Give both the app ID and the app secret, or neither."
NOT_LINKED = "Pick accounts from the list ClipForge found."
NO_LOGIN = "This account is linked through Meta's API, so it has no Chrome login. Use Check instead."
NO_SELFTEST = "This account is linked through Meta's API, so it has no page to check. Use Check instead."
LOG_IN_INSTEAD = "This account logs in with Chrome, so it doesn't take a key. Press Log in instead."
RECONNECT_WITH_FACEBOOK = (
    "This account is linked through a Facebook Page. Link it with Facebook again for a new key."
)
RELOGIN = "The handle changed. Press Log in so ClipForge can check it's the account Chrome is logged into."
CREDIT_NO_CHANNEL = f"Put {CHANNEL} in the credit format, where the channel's name goes."
CREDIT_TOO_LONG = f"Keep the credit format to {CREDIT_MAX} characters."
COLOUR_WRONG = "Give a colour as # and six hex digits, like #FFD60A."
FONT_WRONG = "Pick a font from the list."
LINK_FIELDS = {
    "token_rejected": "token",
    "not_professional": "token",
    "other_account": "token",
    "link_expired": "ticket",
}

Label = Annotated[str, Field(max_length=60)]
Tags = Annotated[list[Annotated[str, Field(max_length=40)]], Field(max_length=10)]
Hashtags = Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=10)]
Token = Annotated[str, Field(max_length=4096)]

router = APIRouter(prefix="/api")


class NewAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: Literal["tiktok", "instagram"]
    method: Literal["browser", "api"]
    label: Label = ""
    handle: Annotated[str, Field(max_length=40)] | None = None  # an Instagram account in Chrome only
    token: Token | None = None  # an Instagram account linked with its own key only
    niche_tags: Tags = []


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Token


class PagesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Token
    app_id: Annotated[str, Field(max_length=64)] | None = None
    app_secret: Annotated[str, Field(max_length=256)] | None = None


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket: Annotated[str, Field(max_length=64)]
    ig_user_ids: Annotated[list[Annotated[str, Field(max_length=64)]], Field(min_length=1, max_length=24)]


class KitChanges(StyleKit):
    """An account's style kit, sent whole, each field's refusal a sentence."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("font", mode="before")
    @classmethod
    def _font(cls, value: Any) -> Any:
        if value is not None and value not in FONTS:
            raise Refusal(FONT_WRONG)
        return value

    @field_validator("accent", "hook_fill", "hook_text", mode="before")
    @classmethod
    def _colour(cls, value: Any) -> Any:
        if value is not None and not (isinstance(value, str) and re.fullmatch(COLOUR, value)):
            raise Refusal(COLOUR_WRONG)
        return value

    @field_validator("credit_format", mode="before")
    @classmethod
    def _credit(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = " ".join((clean_text(value) or "").split())
        if len(value) > CREDIT_MAX:
            raise Refusal(CREDIT_TOO_LONG)
        if CHANNEL not in value:
            raise Refusal(CREDIT_NO_CHANNEL)
        return value


class SettingsChanges(BaseModel):
    """The settings to change. One left out is kept; one sent as null goes back to its default."""

    model_config = ConfigDict(extra="forbid")

    niche_tags: Tags | None = None
    assisted: bool | None = None
    hashtags: Hashtags | None = None
    cta: Annotated[str, Field(max_length=150)] | None = None
    daily_cap: Annotated[int, Field(ge=1, le=10)] | None = None
    windows: list[Any] | None = None
    pair_id: Annotated[str, Field(max_length=64)] | None = None
    creator_rewards: bool | None = None
    style_kit: KitChanges | None = None

    @field_validator("windows")
    @classmethod
    def _windows(cls, value: list[Any] | None) -> list[Window] | None:
        """Checked here, so a refusal names the windows, not one of them."""
        if value is None:
            return None
        if len(value) > MAX_WINDOWS:
            raise Refusal(TOO_MANY_WINDOWS)
        try:
            return [Window.model_validate(w) for w in value]
        except ValidationError:
            raise Refusal(WINDOW_WRONG) from None


class AccountChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label | None = None
    handle: Annotated[str, Field(max_length=40)] | None = None
    paused: bool | None = None
    settings: SettingsChanges | None = None


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _account(engine: Engine, account_id: str) -> Account:
    account = store.get_account(engine.db(), account_id)
    if account is None:
        raise HTTPException(404)
    return account


def _shown(engine: Engine, account_id: str) -> dict[str, Any]:
    return account_json(engine, _account(engine, account_id))


def _label(text: str | None) -> str:
    return " ".join((clean_text(text) or "").split())


def _tags(tags: list[str]) -> list[str]:
    """Niche tags in lower case, each once, in the order given."""
    kept: list[str] = []
    for tag in tags:
        tag = " ".join((clean_text(tag) or "").split()).lower()
        if tag and tag not in kept:
            kept.append(tag)
    return kept


def _typed_handle(text: str | None) -> str:
    """An Instagram handle as typed, without its @ and in lower case, as Instagram keeps them."""
    handle = (text or "").strip().removeprefix("@").lower()
    if not handle:
        raise ApiError(422, "invalid", NO_TYPED_HANDLE, "handle")
    if not INSTAGRAM_HANDLE.fullmatch(handle):
        raise ApiError(
            422, "invalid", "That isn't an Instagram handle. Type it as it shows on the profile.", "handle"
        )
    return handle


def _token(text: str) -> str:
    token = text.strip()
    if not token or any(c.isspace() for c in token):
        raise ApiError(422, "invalid", NO_TOKEN, "token")
    return token


def _link_refused(err: ClipForgeError, token: str) -> ApiError:
    """A link Meta turned down, without the key in what it says."""
    message = err.message.replace(token, "the key") if token else err.message
    return ApiError(400, err.code, message, LINK_FIELDS.get(err.code))


@router.get("/accounts")
def list_accounts(request: Request) -> dict[str, Any]:
    """Every account, oldest first, as its card shows it."""
    engine = _engine(request)
    return {"accounts": [account_json(engine, a) for a in store.list_accounts(engine.db())]}


@router.post("/accounts", status_code=201)
def add_account(body: NewAccount, request: Request) -> dict[str, Any]:
    """A new account: one in Chrome waits for its login, an Instagram one with its own key is linked."""
    engine = _engine(request)
    if body.platform == "tiktok" and body.method == "api":
        raise ApiError(422, "invalid", TIKTOK_API, "method")
    if body.method == "browser" and body.token is not None:
        raise ApiError(422, "invalid", NO_KEY_FOR_BROWSER, "token")
    typed = body.platform == "instagram" and body.method == "browser"
    if body.handle is not None and not typed:
        raise ApiError(422, "invalid", HANDLE_FROM_LOGIN, "handle")
    label, tags = _label(body.label), _tags(body.niche_tags)
    if body.method == "api":
        token = _token(body.token or "")
        try:
            linked = instagram_api.link_instagram(engine, token)
        except ClipForgeError as err:
            raise _link_refused(err, token) from None
        except sqlite3.IntegrityError:
            raise ApiError(409, "handle_taken", HANDLE_TAKEN) from None
        if label or tags:
            settings = linked.settings.model_copy(update={"niche_tags": tags or linked.settings.niche_tags})
            queue.change_account(engine, linked.id, label=label or linked.label, settings=settings)
        return {"account": _shown(engine, linked.id), "job_id": None}
    account = Account(
        id=repo.new_id("a"),
        platform=body.platform,
        method="browser",
        label=label,
        handle=_typed_handle(body.handle) if typed else None,
        status="needs_login",
        settings=AccountSettings(niche_tags=tags),
        created_at=engine.now().timestamp(),
    )
    with engine.lock:
        try:
            store.add_account(engine.db(), account)
        except sqlite3.IntegrityError:
            raise ApiError(409, "handle_taken", HANDLE_TAKEN, "handle") from None
        queue.account_event(engine, account)
    job_id = accounts.start(engine, "account.login", account.id)
    return {"account": _shown(engine, account.id), "job_id": job_id}


@router.post("/accounts/facebook/pages")
def facebook_pages(body: PagesBody, request: Request) -> dict[str, Any]:
    """The Instagram accounts on the person's Facebook Pages, under a ticket Link takes. Nothing is kept."""
    engine = _engine(request)
    if bool(body.app_id) != bool(body.app_secret):
        raise ApiError(422, "invalid", HALF_APP, "app_id" if body.app_secret else "app_secret")
    token = _token(body.token)
    try:
        ticket, found = instagram_api.link_facebook(
            engine, token, app_id=body.app_id, app_secret=body.app_secret
        )
    except ClipForgeError as err:
        raise _link_refused(err, token) from None
    linked = {
        a.ig_user_id: a.id
        for a in store.list_accounts(engine.db())
        if (a.platform, a.method) == ("instagram", "api") and a.ig_user_id
    }
    candidates = [
        {
            "ig_user_id": c.ig_user_id,
            "page_id": c.page_id,
            "page_name": clean_text(c.page_name),
            "username": clean_text(c.username),
            "account_id": linked.get(c.ig_user_id),
        }
        for c in found
    ]
    return {"ticket": ticket, "candidates": candidates}


@router.post("/accounts/facebook/link", status_code=201)
def facebook_link(body: LinkBody, request: Request) -> dict[str, Any]:
    """The accounts picked from a ticket's list linked. A ticket links once."""
    engine = _engine(request)
    held = engine.link_tickets.get(body.ticket)
    if held is not None and not set(body.ig_user_ids) <= {c.ig_user_id for c in held[1]}:
        raise ApiError(422, "invalid", NOT_LINKED, "ig_user_ids")  # the ticket stays usable
    try:
        made = instagram_api.finish_facebook(engine, body.ticket, body.ig_user_ids)
    except ClipForgeError as err:
        raise _link_refused(err, "") from None
    except sqlite3.IntegrityError:
        raise ApiError(409, "handle_taken", HANDLE_TAKEN) from None
    return {"accounts": [account_json(engine, a) for a in made]}


@router.post("/accounts/{account_id}/token")
def new_token(account_id: str, body: TokenBody, request: Request) -> dict[str, Any]:
    """A new key for an Instagram account linked with its own key, which must be the same account's."""
    engine = _engine(request)
    account = _account(engine, account_id)
    if account.method != "api":
        raise ApiError(400, "log_in_instead", LOG_IN_INSTEAD)
    if account.ig_mode == "facebook":
        raise ApiError(400, "reconnect_with_facebook", RECONNECT_WITH_FACEBOOK)
    token = _token(body.token)
    try:
        instagram_api.link_instagram(engine, token, account.id)
    except ClipForgeError as err:
        raise _link_refused(err, token) from None
    except sqlite3.IntegrityError:
        raise ApiError(409, "handle_taken", HANDLE_TAKEN) from None
    return {"account": _shown(engine, account.id)}


@router.post("/accounts/{account_id}/login", status_code=202)
def log_in(account_id: str, request: Request) -> dict[str, Any]:
    """The account's Chrome window opened for its login, once the browser lane is free."""
    engine = _engine(request)
    account = _account(engine, account_id)
    if account.method != "browser":
        raise ApiError(400, "no_login", NO_LOGIN)
    return {
        "account": account_json(engine, account),
        "job_id": accounts.start(engine, "account.login", account.id),
    }


@router.post("/accounts/{account_id}/login/stop", status_code=204)
def stop_login(account_id: str, request: Request) -> Response:
    """The account's login stopped and its window closed, if one is waiting."""
    engine = _engine(request)
    accounts.stop_login(engine, _account(engine, account_id).id)
    return Response(status_code=204)


@router.post("/accounts/{account_id}/check")
def check(account_id: str, request: Request, response: Response) -> dict[str, Any]:
    """Check: an API account is asked now; one in Chrome has its page checked by a job (202)."""
    engine = _engine(request)
    account = _account(engine, account_id)
    if account.method == "browser":
        response.status_code = 202
        job_id = accounts.start(engine, "account.selftest", account.id)
        return {"account": account_json(engine, account), "health": None, "job_id": job_id}
    try:
        health = accounts.check(engine, account)
    except Fatal as err:
        raise ApiError(400, err.code, err.sentence) from None
    return {
        "account": _shown(engine, account.id),
        "health": cleaned(health.model_dump(mode="json")),
        "job_id": None,
    }


@router.post("/accounts/{account_id}/selftest", status_code=202)
def self_test(account_id: str, request: Request) -> dict[str, Any]:
    """The account's upload page opened and checked for what posting needs, by a job."""
    engine = _engine(request)
    account = _account(engine, account_id)
    if account.method != "browser":
        raise ApiError(400, "no_selftest", NO_SELFTEST)
    job_id = accounts.start(engine, "account.selftest", account.id)
    return {"account": account_json(engine, account), "health": None, "job_id": job_id}


@router.patch("/accounts/{account_id}")
def change(account_id: str, body: AccountChanges, request: Request) -> dict[str, Any]:
    """The account's label, typed handle, pause or settings changed, each checked before any is saved."""
    engine = _engine(request)
    sent = body.model_fields_set
    with engine.lock:
        account = _account(engine, account_id)
        fields: dict[str, Any] = {}
        if "label" in sent:
            fields["label"] = _label(body.label)
        if "handle" in sent:
            if (account.platform, account.method) != ("instagram", "browser"):
                raise ApiError(422, "invalid", HANDLE_FROM_LOGIN, "handle")
            handle = _typed_handle(body.handle)
            if handle != account.handle:
                fields |= {"handle": handle, "status": "needs_login", "status_note": RELOGIN}
        mate = None
        if body.settings is not None:
            fields["settings"] = _settings(account, body.settings)
            mate = _mate(engine, account, fields["settings"].pair_id)
        if fields:
            try:
                queue.change_account(engine, account.id, **fields)
            except sqlite3.IntegrityError:
                raise ApiError(409, "handle_taken", HANDLE_TAKEN, "handle") from None
        if body.settings is not None and "pair_id" in body.settings.model_fields_set:
            _pair(engine, account, mate)
        if body.settings is not None and "assisted" in body.settings.model_fields_set:
            queue.assisted_again(engine, account.id)
    if "paused" in sent and body.paused is not None:
        engine.posting.pause_account(account.id, body.paused)
    return {"account": _shown(engine, account.id)}


def _settings(account: Account, changes: SettingsChanges) -> AccountSettings:
    """The account's settings with ``changes`` over them, cleaned."""
    merged = account.settings.model_dump()
    for name in changes.model_fields_set:
        value = getattr(changes, name)
        if value is None:
            value = AccountSettings.model_fields[name].get_default(call_default_factory=True)
        elif name == "niche_tags":
            value = _tags(value)
        elif name == "hashtags":
            value = clean_hashtags([clean_text(t) or "" for t in value])
        elif name == "cta":
            value = " ".join((clean_text(value) or "").split())
        elif name == "style_kit":
            value = value.model_dump()
        merged[name] = value
    return AccountSettings.model_validate(merged)


def _mate(engine: Engine, account: Account, pair_id: str | None) -> Account | None:
    """The account ``pair_id`` names, which must be a live one on the other platform."""
    if pair_id is None:
        return None
    mate = store.get_account(engine.db(), pair_id)
    if mate is None or mate.platform == account.platform:
        raise ApiError(422, "invalid", PAIR_WRONG, "settings.pair_id")
    return mate


def _pair(engine: Engine, account: Account, mate: Account | None) -> None:
    """Pairs kept both ways: the account's old mate and the new mate's old one are let go."""
    if account.settings.pair_id and (mate is None or account.settings.pair_id != mate.id):
        _let_go(engine, account.settings.pair_id, account.id)
    if mate is not None:
        if mate.settings.pair_id and mate.settings.pair_id != account.id:
            _let_go(engine, mate.settings.pair_id, mate.id)
        queue.change_account(
            engine, mate.id, settings=mate.settings.model_copy(update={"pair_id": account.id})
        )


def _let_go(engine: Engine, account_id: str, mate_id: str) -> None:
    """``account_id`` unpaired from ``mate_id``, when it was paired with it."""
    other = store.get_account(engine.db(), account_id)
    if other is not None and other.settings.pair_id == mate_id:
        queue.change_account(engine, other.id, settings=other.settings.model_copy(update={"pair_id": None}))


@router.delete("/accounts/{account_id}", status_code=204)
def remove(account_id: str, request: Request) -> Response:
    """The account removed, as ``accounts.remove`` says."""
    try:
        accounts.remove(_engine(request), account_id)
    except KeyError:
        raise HTTPException(404) from None
    return Response(status_code=204)
