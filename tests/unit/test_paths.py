from clipforge.paths import Paths


def test_paths_default_home(monkeypatch, tmp_path):
    monkeypatch.delenv("CLIPFORGE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Paths.from_env().home == tmp_path / "ClipForge"


def test_paths_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPFORGE_HOME", str(tmp_path / "elsewhere"))
    paths = Paths.from_env()
    assert paths.home == tmp_path / "elsewhere"
    assert paths.db == paths.home / "clipforge.db"
    assert paths.hf_home == paths.home / "assets" / "models" / "hf"
    assert paths.run_dir == paths.home / "run"


def test_paths_ensure_creates_folders(tmp_path):
    paths = Paths(tmp_path / "home")
    paths.ensure()
    for folder in (paths.projects, paths.assets, paths.hf_home, paths.library, paths.logs, paths.run_dir):
        assert folder.is_dir()


def test_project_paths_layout(tmp_path):
    pp = Paths(tmp_path).project("p0123456789")
    root = tmp_path / "projects" / "p0123456789"
    assert pp.root == root
    assert pp.source == root / "source"
    assert pp.work == root / "work"
    assert pp.clips == root / "clips"
    assert pp.info_json == root / "source" / "info.json"
    assert pp.meta_json == root / "source" / "meta.json"
    assert pp.comments_json == root / "source" / "comments.json"
    assert pp.live_chat_json == root / "source" / "chat.live_chat.json"
    assert pp.captions_glob == "source/subs.*.json3"
    assert pp.audio16k == root / "work" / "audio16k.wav"
    assert pp.transcript_json == root / "work" / "transcript.json"
    assert pp.signals_json == root / "work" / "signals.json"
    assert pp.candidates_json == root / "work" / "candidates.json"
    assert pp.llm_dir == root / "work" / "llm"
    pp.ensure()
    assert pp.source.is_dir() and pp.work.is_dir() and pp.clips.is_dir() and pp.llm_dir.is_dir()
