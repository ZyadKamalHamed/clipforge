"""fake_engine opens only a test's own home. A probe or script run outside this suite's conftest has no
CLIPFORGE_HOME, and Engine.open would then open your real ~/ClipForge and write into it: fake_engine
refuses that before anything opens. HOME points at the test's folder here, so even a failure stays in it."""

import pytest

from tests.fakes import fake_engine


def test_fake_engine_refuses_no_home(monkeypatch, tmp_path):
    monkeypatch.delenv("CLIPFORGE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="CLIPFORGE_HOME"):
        fake_engine()
    assert not (tmp_path / "ClipForge").exists()


def test_fake_engine_refuses_the_real_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLIPFORGE_HOME", str(tmp_path / "ClipForge"))
    with pytest.raises(RuntimeError, match="CLIPFORGE_HOME"):
        fake_engine()
    assert not (tmp_path / "ClipForge").exists()


def test_fake_engine_never_finds_the_installed_app():
    """ClipForge.app in your ~/Applications doesn't change what a test's notifications record: the
    notifier looks for its helper in the test's home, where there is none."""
    engine = fake_engine()
    try:
        assert engine.notifier.app_dir == engine.paths.home / "Applications"
        assert not engine.notifier.helper.exists()
    finally:
        engine.close()
