import logging

import pytest

from clipforge.log import redact, setup_logging
from clipforge.paths import Paths

SECRETS = [
    ("Authorization: OAuth EAABsbCS1iHgBAKZBZC0abc123def456", "EAABsbCS1iHgBAKZBZC0abc123def456"),
    ("GET /x?access_token=IGQVJ1234567890abcdefghijklmnop&fields=id", "IGQVJ1234567890abcdefghijklmnop"),
    ('{"access_token": "EAAGm0PX4ZCpsBA123456789"}', "EAAGm0PX4ZCpsBA123456789"),
    ("key sk-ant-api03-AbCdEf1234567890", "sk-ant-api03-AbCdEf1234567890"),
    ("x-goog-api-key: AIzaSyA1234567890abcdefghijklmnopqrstu", "AIzaSyA1234567890abcdefghijklmnopqrstu"),
    ("Cookie: sessionid=abc123; csrftoken=def456", "sessionid=abc123"),
    ("Bearer ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
]


@pytest.mark.parametrize(("text", "secret"), SECRETS)
def test_redact_masks_secrets(text, secret):
    out = redact(text)
    assert secret not in out
    assert "[redacted]" in out


def test_redact_keeps_normal_text():
    assert redact("Downloading audio for arj7oStGLkU") == "Downloading audio for arj7oStGLkU"
    assert redact("GET /x?access_token=IGQVJ1234567890abcdefghijklmnop&fields=id").endswith("&fields=id")


def test_log_file_is_redacted(tmp_path):
    paths = Paths(tmp_path / "home")
    setup_logging(paths, console=False)
    try:
        logging.getLogger("clipforge.test").warning("token=%s", "EAABsbCS1iHgBAKZBZC0abc123def456")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = (paths.logs / "clipforge.log").read_text(encoding="utf-8")
        assert "[redacted]" in text
        assert "EAABsbCS1iHgBAKZBZC0abc123def456" not in text
    finally:
        setup_logging(paths, console=False, file=False)


def test_log_tracebacks_are_redacted(tmp_path):
    paths = Paths(tmp_path / "home")
    setup_logging(paths, console=False)
    try:
        try:
            raise RuntimeError("failed with access_token=EAABsbCS1iHgBAKZBZC0abc123def456")
        except RuntimeError:
            logging.getLogger("clipforge.test").exception("upload failed")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = (paths.logs / "clipforge.log").read_text(encoding="utf-8")
        assert "upload failed" in text and "RuntimeError" in text
        assert "EAABsbCS1iHgBAKZBZC0abc123def456" not in text
    finally:
        setup_logging(paths, console=False, file=False)
