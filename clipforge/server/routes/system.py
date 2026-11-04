"""The routes about ClipForge itself: its health, its settings (SPEC 19), the Doctor page's checks and the
yt-dlp update (SPEC 4.1)."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Body, Request

from clipforge import __version__, service, storage
from clipforge.config import (
    DEFAULT_MODELS,
    ECONOMY_MODELS,
    PASSES,
    SettingError,
    Settings,
    changed,
    choices_of,
)
from clipforge.doctor import run_checks
from clipforge.jobs.runner import Job
from clipforge.models import ProjectOptions
from clipforge.post import queue
from clipforge.server import views
from clipforge.server.errors import ApiError

if TYPE_CHECKING:
    from clipforge.app import Engine

WAIT = "Wait for the downloads to finish, then update."

router = APIRouter(prefix="/api")


def _engine(request: Request) -> Engine:
    return request.app.state.engine


@router.get("/health")
def health() -> dict:
    """Needs no session, so the launcher can tell ClipForge from another app on the port."""
    return {"app": "clipforge", "version": __version__}


def _choices() -> dict[str, list[Any]]:
    """The choices of every setting that has them, and of each of the Create page's options as
    "create_options.<option>"."""
    found = {name: choices_of(f.annotation) for name, f in Settings.model_fields.items()}
    found |= {
        f"create_options.{name}": choices_of(f.annotation) for name, f in ProjectOptions.model_fields.items()
    }
    return {name: values for name, values in found.items() if values is not None}


CHOICES = _choices()


def settings_json(settings: Settings) -> dict[str, Any]:
    """What the Settings page shows: the values, the choices, the passes a model can be set for and the
    models each provider uses when none is set."""
    return {
        "values": settings.model_dump(mode="json"),
        "choices": CHOICES,
        "passes": list(PASSES),
        "models": DEFAULT_MODELS,
        "economy_models": ECONOMY_MODELS,
    }


@router.get("/settings")
def get_settings(request: Request) -> dict[str, Any]:
    return settings_json(_engine(request).settings)


@router.put("/settings")
def put_settings(request: Request, changes: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Some settings changed, each to its whole new value. The settings are checked as a whole, and the
    first problem is a 422 naming its field. TikTok's own schedule turned on or off reaches the posts
    already waiting."""
    engine = _engine(request)
    with engine.lock:  # so two changes never undo each other
        studio = engine.settings.posting.tiktok_schedule
        posting = changes.get("posting")
        if isinstance(posting, dict):  # only Pause posting and Resume move the kill switch
            changes = changes | {"posting": posting | {"paused": engine.settings.posting.paused}}
        try:
            new = changed(engine.settings, changes)
        except SettingError as err:
            raise ApiError(422, "invalid_setting", err.message, err.field) from None
        saved = engine.update_settings(new)
        if saved.posting.tiktok_schedule != studio:
            queue.studio_again(engine)
        return settings_json(saved)


@router.get("/doctor")
def doctor(request: Request) -> dict[str, Any]:
    engine = _engine(request)
    return {"checks": [asdict(check) for check in run_checks(engine.paths, engine.settings)]}


@router.post("/doctor/update-ytdlp")
def update_ytdlp(request: Request) -> dict[str, Any]:
    """Updates yt-dlp while no download runs; downloads that would start wait until it is done."""
    engine = _engine(request)
    with engine.runner.hold("io"):
        if engine.runner.running("io"):
            raise ApiError(409, "downloading", WAIT)
        return asdict(request.app.state.update_ytdlp())


def _service_json(engine: Engine, message: str | None = None) -> dict[str, Any]:
    now = service.status(engine.paths)
    body: dict[str, Any] = {
        "installed": now.installed,
        "running": now.running,
        "pid": now.pid,
        "sentence": now.sentence,
    }
    if message is not None:
        body["message"] = message
    return body


@router.get("/service")
def service_status(request: Request) -> dict[str, Any]:
    return _service_json(_engine(request))


@router.post("/service/install")
def service_install(request: Request) -> dict[str, Any]:
    """Adds the background service. While this engine holds the home, the service's engine waits its turn
    (launchd tries again every 30 seconds) and takes over once this one stops."""
    engine = _engine(request)
    return _service_json(engine, service.install(engine.paths, request.app.state.port))


@router.post("/service/uninstall")
def service_uninstall(request: Request) -> dict[str, Any]:
    engine = _engine(request)
    return _service_json(engine, service.uninstall(engine.paths))


@router.get("/jobs")
def jobs(request: Request, active: bool = False) -> dict[str, Any]:
    """The queued and running jobs, oldest first, or without ``active`` the latest 200 of any status."""
    engine = _engine(request)
    if active:
        sql = "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY id"
    else:
        sql = "SELECT * FROM jobs ORDER BY id DESC LIMIT 200"
    rows = engine.db().execute(sql).fetchall()
    return {"jobs": [views.job_json(engine, Job.from_row(row)) for row in rows]}


@router.get("/storage")
def storage_info(request: Request) -> dict[str, Any]:
    """What each kind of file takes, and what the clean-up would free now."""
    engine = _engine(request)
    found = storage.cleanable(engine.db(), engine.paths, engine.settings, time.time())
    return {
        "usage": asdict(storage.usage(engine.paths)),
        "cleanable": {"projects": len(found), "bytes": sum(size for _, size in found)},
        "delete_sources_after_days": engine.settings.delete_sources_after_days,
    }


@router.post("/storage/clean")
def storage_clean(request: Request) -> dict[str, Any]:
    return asdict(_engine(request).cleanup())
