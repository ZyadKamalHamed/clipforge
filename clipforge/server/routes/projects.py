"""The projects API (SPEC 14.1 to 14.3): links and dropped files become projects at once, each project
reads as the window shows it, and its buttons do what they say: retry, make the clips again, change its
options, delete it."""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import unquote

import anyio.to_thread
from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException

from clipforge.campaigns import service
from clipforge.ingest.urls import NOTHING
from clipforge.models import ProjectOptions
from clipforge.server.errors import INVALID, ApiError
from clipforge.server.uploads import VIDEO_TYPES, receive_upload
from clipforge.server.views import clean_text, project_json

if TYPE_CHECKING:
    from clipforge.app import Engine
    from clipforge.models import Project

NOTHING_TO_RERUN = "This project's clips aren't made yet, so there is nothing to make again."
CAMPAIGN_GONE = "That campaign isn't in ClipForge any more."
TEXT_MAX = 20_000  # characters in one paste: a few hundred links

router = APIRouter(prefix="/api")


class NewProjects(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=TEXT_MAX)
    options: dict[str, Any] = {}
    new: bool = False


class ProjectChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rights_basis: Literal["not_cleared", "own", "cc", "permission", "clipping_programme"] | None = None
    options: dict[str, Any] | None = None
    campaign_id: Annotated[str, Field(max_length=64)] | None = None  # null unlinks


class Rerun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = None


def parse_options(raw: Any, base: ProjectOptions | None = None) -> ProjectOptions:
    """``raw``, a JSON object of options, over ``base`` (or the defaults). An option ClipForge doesn't
    have, or a value it can't take, is a 422 naming it."""
    if not isinstance(raw, dict):
        raise ApiError(422, "invalid", INVALID, "options")
    for key in raw:
        if key not in ProjectOptions.model_fields:
            raise ApiError(422, "invalid", INVALID, f"options.{key}")
    try:
        return ProjectOptions.model_validate((base or ProjectOptions()).model_dump() | raw)
    except ValidationError as err:
        loc = ".".join(str(part) for part in err.errors()[0]["loc"])
        raise ApiError(422, "invalid", INVALID, f"options.{loc}" if loc else "options") from None


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _project(engine: Engine, project_id: str) -> Project:
    project = engine.project(project_id)
    if project is None:
        raise HTTPException(404)
    return project


@router.post("/projects")
def add_projects(body: NewProjects, request: Request) -> dict[str, Any]:
    """A project for each link and file in the text, each set going, and what couldn't be used, with why."""
    engine = _engine(request)
    made, rejected = engine.add_sources(body.text, parse_options(body.options), new=body.new)
    projects = list({p.id: p for p in made}.values())  # the same video twice is one project
    for project in projects:
        engine.start(project.id)
    refused = [{"input": clean_text(r.token), "message": r.reason} for r in rejected]
    if not projects and not refused:
        refused = [{"input": clean_text(body.text.strip()), "message": NOTHING}]
    shown = [project_json(engine, engine.project(p.id) or p) for p in projects]
    return {"projects": shown, "rejected": refused}


@router.post("/projects/upload", status_code=201)
async def upload_project(request: Request) -> dict[str, Any]:
    """A file dropped on the window: the body is the file, ``X-File-Name`` its name (percent-encoded) and
    ``X-Options`` its project's options as JSON."""
    engine = _engine(request)
    try:
        raw = json.loads(request.headers.get("x-options") or "{}")
    except ValueError:
        raise ApiError(422, "invalid", INVALID, "options") from None
    options = parse_options(raw)
    name = unquote(request.headers.get("x-file-name", ""))
    floor = int(engine.settings.min_free_gb * 1e9)
    path = await receive_upload(request, engine.paths.uploads, name, allowed=VIDEO_TYPES, floor_bytes=floor)
    try:
        project = await anyio.to_thread.run_sync(engine.add_file, path, options)
    except BaseException:
        shutil.rmtree(path.parent, ignore_errors=True)
        raise
    return await anyio.to_thread.run_sync(_started, engine, project)


def _started(engine: Engine, project: Project) -> dict[str, Any]:
    engine.start(project.id)
    return project_json(engine, engine.project(project.id) or project)


@router.get("/projects")
def list_projects(
    request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 50, status: str | None = None
) -> dict[str, Any]:
    """The newest projects first; ``status`` (a comma-separated list) keeps those in one of them."""
    engine = _engine(request)
    wanted = [s for s in (status or "").split(",") if s] or None
    return {"projects": [project_json(engine, p) for p in engine.projects(limit, wanted)]}


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    return project_json(engine, _project(engine, project_id))


@router.patch("/projects/{project_id}")
def change_project(project_id: str, body: ProjectChanges, request: Request) -> dict[str, Any]:
    """Changes why the clips may be posted, the options (those left out stay as they are) or the campaign
    the project is linked to: 409 with the sentence when its footage rule doesn't allow the video."""
    engine = _engine(request)
    with engine.lock:
        project = _project(engine, project_id)
        fields: dict[str, Any] = {}
        if body.rights_basis is not None:
            fields["rights_basis"] = body.rights_basis
        if body.options is not None:
            fields["options"] = parse_options(body.options, project.options)
            if "prompt" in body.options:
                fields["prompt"] = fields["options"].prompt  # the project's own prompt follows
        if fields:
            engine.update_project(project_id, **fields)
        if "campaign_id" in body.model_fields_set:
            try:
                service.link_project(engine, project_id, body.campaign_id)
            except LookupError:
                raise ApiError(422, "invalid", CAMPAIGN_GONE, "campaign_id") from None
        return project_json(engine, _project(engine, project_id))


@router.post("/projects/{project_id}/retry")
def retry_project(project_id: str, request: Request) -> dict[str, Any]:
    engine = _engine(request)
    _project(engine, project_id)
    return project_json(engine, engine.retry(project_id))


@router.post("/projects/{project_id}/rerun")
def rerun_project(project_id: str, body: Rerun, request: Request) -> dict[str, Any]:
    """Makes the clips again from the selection on, with a new prompt (or none) when ``prompt`` is given.
    The prompt is kept even when there is nothing to make again yet."""
    engine = _engine(request)
    with engine.lock:
        project = _project(engine, project_id)
        if "prompt" in body.model_fields_set:
            options = parse_options({"prompt": body.prompt}, project.options)
            engine.update_project(project_id, options=options, prompt=options.prompt)
        if not engine.rerun_selection(project_id):
            raise ApiError(409, "nothing_to_rerun", NOTHING_TO_RERUN)
        return project_json(engine, _project(engine, project_id))


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, request: Request) -> Response:
    engine = _engine(request)
    _project(engine, project_id)
    engine.delete_project(project_id)
    return Response(status_code=204)
