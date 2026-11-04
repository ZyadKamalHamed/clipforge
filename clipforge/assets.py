"""What rendering needs besides the code (SPEC 6.1, 8, 20): the YuNet face model and Noto emoji, fetched from
pinned URLs and checked before they are kept, and the fonts that ship inside the package with their
licences. ``fetch_all`` gets the face and speech models at install, so the first run doesn't wait."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from clipforge.errors import ClipForgeError
from clipforge.paths import Paths

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"

FONTS = {
    "montserrat-black": "Montserrat-Black.ttf",
    "montserrat-extrabold": "Montserrat-ExtraBold.ttf",
    "montserrat-bold": "Montserrat-Bold.ttf",
    "poppins-extrabold": "Poppins-ExtraBold.ttf",
    "anton": "Anton-Regular.ttf",
    "bangers": "Bangers-Regular.ttf",
    "inter-extrabold": "Inter-ExtraBold.ttf",
    "bebas-neue": "BebasNeue-Regular.ttf",
    "archivo-black": "ArchivoBlack-Regular.ttf",
    "luckiest-guy": "LuckiestGuy-Regular.ttf",
}

LICENCES = {  # family -> its licence text beside the fonts (OFL 1.1, Luckiest Guy Apache 2.0)
    "Montserrat": "OFL-Montserrat.txt",
    "Poppins": "OFL-Poppins.txt",
    "Anton": "OFL-Anton.txt",
    "Bangers": "OFL-Bangers.txt",
    "Inter": "OFL-Inter.txt",
    "Bebas Neue": "OFL-BebasNeue.txt",
    "Archivo Black": "OFL-ArchivoBlack.txt",
    "Luckiest Guy": "LICENSE-LuckiestGuy.txt",
}

Fetch = Callable[[str, int], bytes]  # (url, max_bytes) -> the body


@dataclass(frozen=True)
class Asset:
    file: str
    urls: tuple[str, ...]  # tried in order
    size: int
    sha256: str


MODELS = {
    "yunet": Asset(
        "face_detection_yunet_2023mar.onnx",
        (
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
            "face_detection_yunet_2023mar.onnx",
            "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        ),
        232_589,
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
}


def font_path(name: str) -> Path:
    return FONTS_DIR / FONTS[name]


def url_allowed(url: str, hosts: frozenset[str]) -> bool:
    """Whether ``url`` is https to one of ``hosts`` on the usual port, with no user name or password in it."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname in hosts
        and port in (None, 443)
        and parts.username is None
        and parts.password is None
    )


def fetch_bytes(url: str, *, max_bytes: int, hosts: frozenset[str] | None = None) -> bytes:
    """GET ``url`` (redirects followed, 30 s timeout), refusing a body over ``max_bytes``. With ``hosts``,
    every request on the way, each redirect included, must pass ``url_allowed`` before it is sent."""

    def check(request: httpx.Request) -> None:
        if hosts is not None and not url_allowed(str(request.url), hosts):
            raise ValueError(f"{request.url.host} isn't a host this may be fetched from")

    body = bytearray()
    client = httpx.Client(follow_redirects=True, timeout=30.0, event_hooks={"request": [check]})
    with client, client.stream("GET", url) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > max_bytes:
                raise ValueError(f"more than {max_bytes} bytes")
    return bytes(body)


def write_bytes(dest: Path, data: bytes) -> None:
    """A temp file beside ``dest``, then a rename, so nothing ever reads half a file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _matches(data: bytes, asset: Asset) -> bool:
    return len(data) == asset.size and hashlib.sha256(data).hexdigest() == asset.sha256


def model_ready(paths: Paths, name: str = "yunet") -> bool:
    asset = MODELS[name]
    path = paths.models / asset.file
    return path.is_file() and _matches(path.read_bytes(), asset)


def ensure_model(paths: Paths, name: str = "yunet", *, fetch: Fetch | None = None) -> Path:
    """The model's path, downloaded on first use from each pinned URL in turn. Only bytes of the pinned
    size and SHA-256 are kept."""
    asset = MODELS[name]
    dest = paths.models / asset.file
    if model_ready(paths, name):
        return dest
    get = fetch or (lambda url, max_bytes: fetch_bytes(url, max_bytes=max_bytes))
    problems = []
    for url in asset.urls:
        try:
            data = get(url, asset.size)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            problems.append(f"{url}: {exc}")
            continue
        if not _matches(data, asset):
            problems.append(f"{url}: not the pinned file (its size or checksum differs)")
            continue
        write_bytes(dest, data)
        return dest
    raise ClipForgeError(
        "asset_failed",
        f"Couldn't download the {name} model ClipForge uses to find faces. "
        "Check the internet connection, then render again.",
        details="; ".join(problems),
    )


def speech_engine(paths: Paths):
    """The default speech engine, whose model install fetches (Whisper's comes when it is first chosen)."""
    from clipforge.asr.parakeet import ParakeetEngine

    return ParakeetEngine(paths.hf_home)


def fetch_all(paths: Paths) -> Iterator[str]:
    """The face model, then the speech model, each fetched unless it is already here, with a line for each
    as it goes. A download that fails raises its ClipForgeError."""
    if model_ready(paths):
        yield "The face model is ready."
    else:
        ensure_model(paths)
        yield "Downloaded the face model."
    speech = speech_engine(paths)
    if speech.model_ready():
        yield "The speech model is ready."
        return
    yield f"Downloading the speech model ({speech.size}). It takes a while the first time."
    speech.prepare()
    yield "Downloaded the speech model."
