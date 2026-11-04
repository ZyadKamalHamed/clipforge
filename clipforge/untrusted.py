"""Text from outside ClipForge (a video's title, a comment, something typed into the window) made safe to
show and to draw: C0 and C1 control characters become spaces, and the marks that turn text around
(U+202A to U+202E and U+2066 to U+2069) go, so a title can't make "gnp.exe" read as "exe.png"."""

from __future__ import annotations

CONTROLS = {c: " " for c in (*range(0x00, 0x20), *range(0x7F, 0xA0))}
TURNS = {c: None for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))}
CLEAN = str.maketrans(CONTROLS | TURNS)


def clean_text(s: str | None) -> str | None:
    """``s`` safe to show: control characters as spaces, and no marks that turn the text around."""
    return None if s is None else s.translate(CLEAN)


def clean_lines(s: str | None) -> str | None:
    """``s`` as ``clean_text`` makes it, line breaks kept: for text written in lines, like a caption."""
    return None if s is None else "\n".join(line.translate(CLEAN) for line in s.split("\n"))
