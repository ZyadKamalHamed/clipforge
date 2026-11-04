"""The guides in docs/guides (Phase 5g, Phase 6): the setup and look guides exist, every link between the
guides lands on a file and, with a #, on one of its headings, and the names every guide sets in bold are
names the window shows."""

import re
from pathlib import Path

from clipforge.today.path import LEVELS

ROOT = Path(__file__).resolve().parents[2]
GUIDES = ROOT / "docs" / "guides"
LINK = re.compile(r"\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
QUOTED = ("Install", "Download", "Use this take", "Trial reel", "Cold open")
HEADING = re.compile(r"^#+ (.+)$", re.M)
# Names the window builds from a template, with the template's fixed part.
TEMPLATED = {
    "Add 6 accounts": "`Add ${n} account",
    "Copy link and open Whop": "`Copy link and open ${marketplace}`",
    "Every Instagram post": "Every {PLATFORMS[platform]} post",
}
# Bold in a guide but not a name in the window: Meta's own button and the heads of campaigns.md's lists.
ELSEWHERE = {
    "Generate Access Token",
    "A project you started by hand",
    "Instagram posts made through the API",
    "TikTok posts, and Instagram posts made another way",
}
# Names the window shows as the server sends them: the levels of the path on Today.
SERVED = {level.name for level in LEVELS}


def window_copy() -> str:
    """Every line of the window's source but its tests."""
    files = [*(ROOT / "web" / "src").rglob("*.ts"), *(ROOT / "web" / "src").rglob("*.tsx")]
    return "\n".join(p.read_text(encoding="utf-8") for p in files if not p.name.endswith(".test.ts"))


def anchor(heading: str) -> str:
    """The #part a link to ``heading`` takes, as GitHub makes it."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_guides_name_what_the_app_says():
    setup, look = GUIDES / "setup.md", GUIDES / "look.md"
    assert setup.is_file() and look.is_file()
    for guide in sorted(GUIDES.glob("*.md")):
        for target in LINK.findall(guide.read_text(encoding="utf-8")):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue  # a web address or a heading on the same page
            path, _, part = target.partition("#")
            assert (guide.parent / path).exists(), f"{guide.name} links to {target}"
            if part:
                headings = HEADING.findall((guide.parent / path).read_text(encoding="utf-8"))
                assert part in {anchor(h) for h in headings}, (
                    f"{guide.name} links to {target}, no such heading"
                )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/guides/setup.md" in readme and "docs/guides/look.md" in readme
    copy = window_copy()
    named = set(BOLD.findall(setup.read_text(encoding="utf-8") + look.read_text(encoding="utf-8")))
    trial = set(BOLD.findall((GUIDES / "instagram.md").read_text(encoding="utf-8")))
    assert set(QUOTED) <= named | trial
    for name in sorted(named | set(QUOTED)):
        assert name in copy, f"the guides name {name}, which the window never shows"


def test_every_guide_names_only_what_the_window_shows():
    """A bold name in any guide is a button, tab, field or setting the window shows; a bold lead-in ends in a
    full stop or a colon, and a name the window builds from a template is checked by its template."""
    copy = window_copy()
    for guide in sorted(GUIDES.glob("*.md")):
        for name in sorted(set(BOLD.findall(guide.read_text(encoding="utf-8")))):
            if name.endswith((".", ":")) or name in ELSEWHERE | SERVED:
                continue
            assert TEMPLATED.get(name, name) in copy, (
                f"{guide.name} names {name}, which the window never shows"
            )


def section(text: str, heading: str) -> str:
    """The part of a Markdown page under ``heading``, up to the next heading of that level or higher."""
    level = heading.split(" ")[0]
    start = text.index(heading + "\n")
    stop = re.compile(rf"^#{{1,{len(level)}}} ", re.M).search(text, start + len(heading))
    return text[start : stop.start() if stop else len(text)]


def test_readme_and_setup_name_what_install_checks():
    """README's Install and setup.md's step 1 list everything scripts/install.sh stops for."""
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    needs = {re.sub(r" \(.*\)$", "", name) for name in re.findall(r'need "([^"]+)"', script)}
    assert {"Homebrew", "ffmpeg", "uv", "Google Chrome"} <= needs
    readme = section((ROOT / "README.md").read_text(encoding="utf-8"), "## Install")
    setup = section((GUIDES / "setup.md").read_text(encoding="utf-8"), "## 1. Install")
    for name in sorted(needs):
        assert name in readme, f"README's Install doesn't say you need {name}"
        assert name in setup, f"setup.md's step 1 doesn't say you need {name}"
