import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".venv",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".superpowers",
    "dist",
}
SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".json",
    ".txt",
    ".sh",
    ".sql",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".yml",
    ".yaml",
}
DASHES = {chr(0x2014): "em dash", chr(0x2013): "en dash"}
BUILT = ROOT / "clipforge" / "server" / "static"  # the window as Vite builds it from web/


def repo_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        here = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info") and here / d != BUILT
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix in SUFFIXES:
                yield path


def test_no_em_or_en_dashes():
    found = []
    for path in repo_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for ch, name in DASHES.items():
                if ch in line:
                    found.append(f"{path.relative_to(ROOT)}:{n}: {name}")
    assert not found, "\n".join(found)


def test_dash_scan_covers_the_web_app_and_scripts():
    scanned = {p.relative_to(ROOT).as_posix() for p in repo_files()}
    assert {"web/index.html", "web/src/main.tsx", "web/vite.config.ts"} <= scanned
    scripts = ROOT / "scripts"
    assert {p.relative_to(ROOT).as_posix() for p in scripts.glob("*.sh")} <= scanned
    assert not [p for p in scanned if p.startswith(("web/node_modules/", "clipforge/server/static/"))]


# Text from videos is untrusted, so the window only ever renders it as React text, and it never asks
# anything with a browser dialog (SPEC 14.1: "Toasts for short confirmations; never a browser alert").
FORBIDDEN_IN_WEB = {
    "a browser dialog": re.compile(r"\b(?:alert|confirm|prompt)\s*\("),
    "raw HTML": re.compile(r"dangerouslySetInnerHTML|innerHTML|outerHTML|insertAdjacentHTML|document\.write"),
    "a leave-page prompt": re.compile(r"beforeunload"),
    # The purple is for fills and icons: as text it falls under AA on the panel in both themes.
    "purple text on hover": re.compile(r"hover:text-accent\b"),
}


def forbidden_in(line: str) -> list[str]:
    return [what for what, pattern in FORBIDDEN_IN_WEB.items() if pattern.search(line)]


def test_forbidden_web_patterns_catch_what_they_mean():
    for line in (
        'window.alert("x")',
        "if (confirm (text)) go();",
        "el.innerHTML = html;",
        "<div dangerouslySetInnerHTML={x} />",
        'addEventListener("beforeunload", stop);',
        'className="text-fg underline hover:text-accent"',
    ):
        assert forbidden_in(line), line
    for line in (
        "const ask = useConfirm();",
        "setPrompt(text);",
        "<label>Prompt (optional)</label>",
        '<Upload className="text-accent" />',
        'className="hover:bg-accent-deep"',
    ):
        assert not forbidden_in(line), line


def test_web_never_sets_html_or_opens_browser_dialogs():
    web = ROOT / "web"
    files = sorted(p for p in (web / "src").rglob("*") if p.suffix in (".ts", ".tsx"))
    files.append(web / "index.html")
    assert len(files) > 5
    found = []
    for path in files:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            found += [f"{path.relative_to(ROOT)}:{n}: {what}" for what in forbidden_in(line)]
    assert not found, "\n".join(found)
