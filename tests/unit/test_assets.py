import hashlib

import pytest
from PIL import ImageFont

from clipforge import assets
from clipforge.config import Settings
from clipforge.db import connect, migrate
from clipforge.doctor import run_checks
from clipforge.errors import ClipForgeError
from clipforge.paths import Paths


def fake_model(monkeypatch, data: bytes) -> assets.Asset:
    """Point the YuNet entry at a few bytes, so no test downloads anything."""
    real = assets.MODELS["yunet"]
    asset = assets.Asset(
        real.file,
        ("https://a.invalid/model.onnx", "https://b.invalid/model.onnx"),
        len(data),
        hashlib.sha256(data).hexdigest(),
    )
    monkeypatch.setitem(assets.MODELS, "yunet", asset)
    return asset


def test_migration_3_adds_render_tables(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert migrate(conn) >= 3  # test_db pins the latest version
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"renders", "library_items", "gameplay_uses"} <= tables
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(renders)")}
    assert {"clip_id", "version", "style_key", "path", "cover_path", "edl_path", "qa_json", "status"} <= cols
    conn.close()


def test_project_paths_for_clips(tmp_path):
    paths = Paths(tmp_path)
    pp = paths.project("p1")
    assert pp.sections == tmp_path / "projects" / "p1" / "source" / "sections"
    assert pp.clip_dir("c1") == tmp_path / "projects" / "p1" / "clips" / "c1"
    assert pp.analysis_json("c1") == pp.clip_dir("c1") / "analysis.json"
    assert pp.render_dir("c1", 2) == pp.clip_dir("c1") / "v2"
    assert paths.models == tmp_path / "assets" / "models"
    assert paths.emoji == tmp_path / "assets" / "emoji"
    assert paths.gameplay == tmp_path / "library" / "gameplay"


def test_render_settings_defaults():
    s = Settings()
    assert (s.encoder, s.fps_cap_30, s.caption_preset, s.accent_color) == ("fast", False, "bold_pop", None)
    assert s.punch_ins and s.pause_trim and s.filler_removal and s.credit_line and s.emoji and s.sfx
    assert not s.auto_pace and not s.hook_whole_clip
    assert s.gameplay_mode == "auto"


def test_ensure_model_checks_size_and_hash(tmp_path, monkeypatch):
    fake_model(monkeypatch, b"right bytes")
    with pytest.raises(ClipForgeError) as err:
        assets.ensure_model(Paths(tmp_path), fetch=lambda url, max_bytes: b"wrong bytes")
    assert err.value.code == "asset_failed"
    assert not list(Paths(tmp_path).models.glob("*"))


def test_ensure_model_tries_the_mirror(tmp_path, monkeypatch):
    asset = fake_model(monkeypatch, b"model bytes")
    seen = []

    def fetch(url, max_bytes):
        seen.append(url)
        if url == asset.urls[0]:
            raise OSError("offline")
        return b"model bytes"

    path = assets.ensure_model(Paths(tmp_path), fetch=fetch)
    assert path.read_bytes() == b"model bytes"
    assert seen == list(asset.urls)


def test_ensure_model_is_cached(tmp_path, monkeypatch):
    fake_model(monkeypatch, b"model bytes")
    assets.ensure_model(Paths(tmp_path), fetch=lambda url, max_bytes: b"model bytes")

    def no_fetch(url, max_bytes):
        raise AssertionError("fetched again")

    assert assets.ensure_model(Paths(tmp_path), fetch=no_fetch).exists()
    assert assets.model_ready(Paths(tmp_path))


def test_every_bundled_font_loads_and_has_a_licence():
    for name in assets.FONTS:
        font = ImageFont.truetype(str(assets.font_path(name)), 72)
        assert font.getbbox("Ag")[2] > 0, name
    for family, licence in assets.LICENCES.items():
        text = (assets.FONTS_DIR / licence).read_text(encoding="utf-8")
        assert "LICENSE" in text.upper(), family


def test_doctor_reports_render_tools(tmp_path):
    checks = {c.name: c for c in run_checks(Paths(tmp_path), Settings())}
    assert checks["opencv"].ok
    assert checks["fonts"].ok
    assert checks["video encoder"].ok
    assert checks["face model"].required is False
