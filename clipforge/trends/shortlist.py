"""The TikTok sound shortlist (SPEC 13): sounds you paste from TikTok as links, kept with a title, the moods
and energy they suit and the region they trend in. A link is read as text and never fetched: its id and
the name in it are all ClipForge keeps, and its address is rebuilt on www.tiktok.com from those two. Adding
a sound already on the list writes over it and keeps when it was first added, so it doesn't turn fresh.
Instagram's trending rows share the table but are read, never changed, here."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, get_args
from urllib.parse import quote, unquote, urlsplit

from clipforge.brain.schemas import Mood
from clipforge.db import transaction
from clipforge.edits import EditError
from clipforge.repo import new_id
from clipforge.trends import store
from clipforge.trends.models import Sound
from clipforge.untrusted import clean_text

if TYPE_CHECKING:
    from clipforge.app import Engine

UNTITLED = "Untitled sound"
NOT_A_SOUND = "That isn't a TikTok sound link. Copy it from a sound's page in TikTok."
SHORT_LINK = "Open that short link in your browser and paste the full tiktok.com/music link it shows."
NO_SUCH_MOOD = "Pick moods from ClipForge's list."
NO_TITLE = "Give the sound a title."
TITLE_CHARS = 200
LONG_TITLE = f"Keep the title to {TITLE_CHARS} characters."
ENERGY = "Energy goes from 1 to 5."
REGION = "Pick AU or US, or no region."
HOSTS = ("tiktok.com", "www.tiktok.com", "m.tiktok.com")
SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com")  # TikTok's short links, which only a browser follows
LINK_CHARS = 2000  # a pasted link longer than this isn't one TikTok made
MOODS: tuple[str, ...] = get_args(Mood)
FIELDS = ("title", "region", "moods", "energy")

_MUSIC = re.compile(r"/music/(?:(?P<slug>[^/]+)-)?(?P<id>\d{15,20})/?")


def _title(value: str | None) -> str:
    """``value`` cleaned for showing, its runs of spaces made one; "" when nothing is left."""
    return " ".join((clean_text(value) or "").split())


def parse_tiktok_music(text: str) -> tuple[str, str, str]:
    """The sound a pasted TikTok link is for: its id, a title from the name in the link (``UNTITLED`` when
    it has none) and its address on www.tiktok.com. Raises ``EditError`` on "link" for a short link, which
    only a browser can follow, and for anything that isn't a sound's page on tiktok.com."""
    link = text.strip()
    if not link or len(link) > LINK_CHARS or not link.isprintable() or any(c.isspace() for c in link):
        raise EditError("link", NOT_A_SOUND)
    if "://" not in link:
        link = "https://" + link
    try:
        parts = urlsplit(link)
        port = parts.port
    except ValueError:
        raise EditError("link", NOT_A_SOUND) from None
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or port is not None:
        raise EditError("link", NOT_A_SOUND)
    if parts.hostname in SHORT_HOSTS:
        raise EditError("link", SHORT_LINK)
    found = _MUSIC.fullmatch(parts.path)
    if parts.hostname not in HOSTS or found is None:
        raise EditError("link", NOT_A_SOUND)
    sound_id = found["id"]
    title = _title(unquote(found["slug"] or "").replace("-", " "))[:TITLE_CHARS].rstrip()
    if not title:
        return sound_id, UNTITLED, f"https://www.tiktok.com/music/{sound_id}"
    return (
        sound_id,
        title,
        f"https://www.tiktok.com/music/{quote(title.replace(' ', '-'), safe='')}-{sound_id}",
    )


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    """The shortlist fields among ``fields`` checked and cleaned, as the stored sound names them; raises
    ``EditError`` naming the first one that is wrong."""
    kept: dict[str, Any] = {}
    if "title" in fields:
        title = _title(fields["title"])
        if not title:
            raise EditError("title", NO_TITLE)
        if len(title) > TITLE_CHARS:
            raise EditError("title", LONG_TITLE)
        kept["title"] = title
    if "region" in fields:
        if fields["region"] not in ("AU", "US", None):
            raise EditError("region", REGION)
        kept["region"] = fields["region"]
    if "moods" in fields:
        moods = fields["moods"] or []
        if not isinstance(moods, list) or any(m not in MOODS for m in moods):
            raise EditError("moods", NO_SUCH_MOOD)
        kept["mood_tags"] = list(dict.fromkeys(moods))
    if "energy" in fields:
        energy = fields["energy"]
        if energy is not None and (type(energy) is not int or not 1 <= energy <= 5):
            raise EditError("energy", ENERGY)
        kept["energy"] = energy
    return kept


def add(
    engine: Engine,
    link: str,
    *,
    title: str | None = None,
    region: str | None = None,
    moods: list[str] | None = None,
    energy: int | None = None,
) -> Sound:
    """Puts the sound ``link`` is for on the shortlist, titled ``title`` or, when that is blank, by the name
    in its link, or writes over it when it is there already (keeping when it was added). Raises
    ``EditError`` naming what is wrong."""
    sound_id, named, url = parse_tiktok_music(link)
    fields = _clean({"region": region, "moods": moods, "energy": energy})
    if _title(title):
        fields |= _clean({"title": title})
    sound = Sound(
        id=new_id("snd"),
        platform="tiktok",
        source="shortlist",
        external_id=sound_id,
        title=fields.pop("title", named),
        url=url,
        created_at=engine.now().timestamp(),
        **fields,
    )
    conn = engine.db()
    with transaction(conn):
        return store.put_shortlist(conn, sound)


def _shortlisted(conn: Any, sound_id: str) -> Sound:
    sound = store.get_sound(conn, sound_id)
    if sound is None or sound.source != "shortlist":
        raise LookupError(sound_id)
    return sound


def update(engine: Engine, sound_id: str, **fields: Any) -> Sound:
    """The shortlisted sound with the given ``title``, ``region``, ``moods`` and ``energy`` changed (None
    clears a region or an energy). Raises LookupError when it isn't on the shortlist, and ``EditError``
    naming a field that is wrong."""
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise TypeError(f"not a shortlist field: {sorted(unknown)}")
    conn = engine.db()
    with transaction(conn):
        _shortlisted(conn, sound_id)
        return store.update_sound(conn, sound_id, **_clean(fields))  # type: ignore[return-value]


def remove(engine: Engine, sound_id: str) -> None:
    """Takes the sound off the shortlist. Raises LookupError when it isn't on it."""
    conn = engine.db()
    with transaction(conn):
        _shortlisted(conn, sound_id)
        store.delete_sound(conn, sound_id)
