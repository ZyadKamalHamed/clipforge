"""Every sentence that tells you to press something names a button the window really has, so nobody hunts
for a "Retry" the Queue calls something else. The platforms' and Meta's own buttons, pressed on their own
pages, are named as those pages name them."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRESS = re.compile(r"\b[Pp]ress ([A-Z][^.,!?\n\"`]*)")
QUOTED = re.compile(r"""["'`]([A-Z][\w' ]*)["'`]""")
JSX_TEXT = re.compile(r"^\s*([A-Z][\w' ]*)\s*$", re.M)
THEIRS = {"Post", "Schedule", "Share", "Generate token", "Extend Access Token"}  # on the sites' own pages
# What may follow a button's name: why or where it's pressed, or when.
AFTER = (" to ", " so ", " instead", " tomorrow", " beside ", " in ")


def strings(paths: list[Path]) -> list[str]:
    texts = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                texts.append(node.value)
    return texts


def engine_texts() -> list[str]:
    return strings(sorted((ROOT / "clipforge").rglob("*.py")))


def pressed(texts: list[str]) -> set[str]:
    return {
        part.strip()
        for text in texts
        for match in PRESS.finditer(text)
        for part in match.group(1).split(" or ")
    }


def web_files() -> list[Path]:
    src = ROOT / "web" / "src"
    return [p for p in sorted(src.rglob("*.ts*")) if not p.name.endswith(".test.ts")]


def labels() -> set[str]:
    found = set(THEIRS)
    for path in web_files():
        text = path.read_text()
        found |= {m.group(1).strip() for m in QUOTED.finditer(text)}
        found |= {m.group(1).strip() for m in JSX_TEXT.finditer(text)}
    return found


def named(phrase: str, known: set[str]) -> bool:
    """The phrase starts with a known button's whole name, and anything after says why, where or when."""
    for label in sorted(known, key=len, reverse=True):
        rest = phrase[len(label) :]
        if phrase.startswith(label) and (not rest or rest.startswith(AFTER)):
            return True
    return False


def test_every_press_names_a_button_the_window_has():
    known = labels()
    phrases = pressed(engine_texts() + [p.read_text() for p in web_files()])
    assert "Mark as posted" in phrases  # the scan reaches the Queue's sentences
    assert sorted(p for p in phrases if not named(p, known)) == []


def test_a_posts_sentence_names_its_queue_rows_buttons():
    """A post's sentence shows on its row in the Queue, so the buttons it names are that row's. (The login
    sentences show on the account's card, and the plan's in the Post dialog.)"""
    row = (ROOT / "web" / "src" / "components" / "PostRow.tsx").read_text()
    known = set(re.findall(r'"([^"]+)"', row.split("const LABELS", 1)[1].split("};", 1)[0])) | THEIRS
    shown = [
        p
        for p in sorted((ROOT / "clipforge" / "post").rglob("*.py"))
        if p.name not in ("login.py", "plan.py")
    ]
    phrases = pressed(strings(shown))
    assert "Mark as posted" in phrases
    assert sorted(p for p in phrases if not named(p, known)) == []
