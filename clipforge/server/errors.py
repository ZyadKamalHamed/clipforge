"""How the API says no: one JSON shape with a code, one plain sentence and the field it is about."""

from __future__ import annotations

from fastapi.responses import JSONResponse

INVALID = "ClipForge couldn't read that request."


class Refusal(ValueError):
    """Raised by a request body's validator with a sentence for the person, which the 422 says as it is."""


class ApiError(Exception):
    """Raised by a route to answer with ``api_error``."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field


def api_error(status: int, code: str, message: str, field: str | None = None, **kw) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, "field": field}, status_code=status, **kw)
