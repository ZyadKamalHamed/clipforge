"""Logging with secrets masked before anything reaches a file or the terminal (SPEC 18)."""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys

from clipforge.paths import Paths

MASK = "[redacted]"

_SECRET_KEYS = r"access_token|refresh_token|client_secret|api_key|fb_exchange_token|token|password"

# Order matters: whole header values first, then key=value pairs, then bare token shapes.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?i)\b(authorization|set-cookie|cookie|x-goog-api-key|x-api-key)([\"']?\s*:\s*[\"']?)[^\"'\r\n]+"
        ),
        rf"\1\2{MASK}",
    ),
    (re.compile(r"(?i)\b(OAuth|Bearer)\s+[A-Za-z0-9._~+/=\-]+"), rf"\1 {MASK}"),
    (re.compile(rf"(?i)(?<![\w-])((?:{_SECRET_KEYS}|key)=)[^&\s\"'#]+"), rf"\1{MASK}"),
    (re.compile(rf"(?i)(\"(?:{_SECRET_KEYS})\"\s*:\s*\")[^\"]*(\")"), rf"\1{MASK}\2"),
    (re.compile(rf"(?i)('(?:{_SECRET_KEYS})'\s*:\s*')[^']*(')"), rf"\1{MASK}\2"),
    (re.compile(r"\bEAA[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bIG[A-Za-z0-9_\-]{30,}"), MASK),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), MASK),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactFilter(logging.Filter):
    """Masks the message, the traceback and the stack text of every record it sees."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a bad format string must not crash the caller
            message = f"{record.msg} {record.args}"
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


_OURS = "_clipforge_handler"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(paths: Paths, *, verbose: bool = False, console: bool = True, file: bool = True) -> None:
    """Install ClipForge's handlers on the root logger, replacing any it installed before.

    The file gets everything from ClipForge and warnings from libraries; the terminal gets
    warnings unless ``verbose``. ``file=False, console=False`` removes them all.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _OURS, False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(logging.WARNING)
    logging.getLogger("clipforge").setLevel(logging.DEBUG)

    handlers: list[logging.Handler] = []
    if file:
        paths.logs.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            paths.logs / "clipforge.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        handlers.append(fh)
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.DEBUG if verbose else logging.WARNING)
        handlers.append(ch)
    for handler in handlers:
        handler.addFilter(RedactFilter())
        handler.setFormatter(logging.Formatter(_FORMAT))
        setattr(handler, _OURS, True)
        root.addHandler(handler)
