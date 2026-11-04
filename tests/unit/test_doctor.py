from clipforge.config import Settings
from clipforge.doctor import run_checks
from clipforge.paths import Paths


def checks_by_name(checks):
    return {c.name: c for c in checks}


def test_doctor_flags_missing_ffmpeg(tmp_path):
    checks = checks_by_name(
        run_checks(Paths(tmp_path), Settings(), which=lambda name: None, user_home=tmp_path)
    )
    ffmpeg = checks["ffmpeg"]
    assert ffmpeg.required is True
    assert ffmpeg.ok is False
    assert ffmpeg.fix


def test_doctor_reports_found_tools(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    found = {"ffmpeg": fake_bin / "ffmpeg", "ffprobe": fake_bin / "ffprobe"}
    checks = checks_by_name(
        run_checks(Paths(tmp_path), Settings(), which=lambda name: found.get(name), user_home=tmp_path)
    )
    assert checks["ffmpeg"].ok and str(found["ffmpeg"]) in checks["ffmpeg"].detail
    assert checks["ffprobe"].ok
    assert checks["python"].ok


def test_doctor_parakeet_model_missing_is_not_required(tmp_path):
    checks = checks_by_name(
        run_checks(Paths(tmp_path), Settings(), which=lambda name: None, user_home=tmp_path)
    )
    model = checks["speech model"]
    assert model.ok is False
    assert model.required is False
    assert "download" in model.fix.lower()


def test_doctor_disk_check_uses_min_free(tmp_path):
    checks = checks_by_name(
        run_checks(
            Paths(tmp_path), Settings(min_free_gb=10_000_000.0), which=lambda name: None, user_home=tmp_path
        )
    )
    assert checks["disk"].ok is False
    assert checks["disk"].required is True


def test_doctor_finds_claude_where_its_installer_puts_it(tmp_path):
    """A PATH without ~/.local/bin (a Terminal set up without it, or an app opened from Finder) still finds
    the claude command the AI provider uses, so the check doesn't say "not found" while clips are picked."""
    claude = tmp_path / ".local" / "bin" / "claude"
    claude.parent.mkdir(parents=True)
    claude.write_text('#!/bin/sh\necho "2.1.0 (Claude Code)"\n')
    claude.chmod(0o755)
    checks = checks_by_name(
        run_checks(Paths(tmp_path), Settings(), which=lambda name: None, user_home=tmp_path)
    )
    assert checks["claude"].ok and checks["claude"].detail == f"2.1.0 (Claude Code) at {claude}"


def test_doctor_checks_for_google_chrome_without_requiring_it(tmp_path):
    apps = tmp_path / "Apps"
    checks = checks_by_name(
        run_checks(Paths(tmp_path), Settings(), which=lambda name: None, user_home=tmp_path, apps=apps)
    )
    chrome = checks["chrome"]
    assert (chrome.ok, chrome.required) == (False, False)
    assert "TikTok" in chrome.fix
    (tmp_path / "Applications" / "Google Chrome.app").mkdir(parents=True)
    found = checks_by_name(
        run_checks(Paths(tmp_path), Settings(), which=lambda name: None, user_home=tmp_path, apps=apps)
    )["chrome"]
    assert found.ok and "Google Chrome.app" in found.detail


def test_doctor_notes_the_background_service_without_requiring_it(tmp_path):
    checks = checks_by_name(
        run_checks(Paths(tmp_path), Settings(), which=lambda name: None, user_home=tmp_path)
    )
    background = checks["background"]
    assert (background.ok, background.required) == (False, False)
    assert (
        background.detail
        == "Not installed, so ClipForge runs from when you open it until you log out or restart the Mac."
    )
    assert background.fix == "Run clipforge service install to keep ClipForge running after you log in."
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.clipforge.engine.plist").write_bytes(b"")
    found = checks_by_name(
        run_checks(Paths(tmp_path), Settings(), which=lambda name: None, user_home=tmp_path)
    )
    assert found["background"].ok and found["background"].detail == "Installed: starts when you log in."
