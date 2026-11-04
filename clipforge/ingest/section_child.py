"""A section download in a process of its own (SPEC 7.2), so ClipForge can stop it, with the ffmpeg and
deno that yt-dlp starts, at its time limit or when its job is cancelled; and so it stops by itself once
ClipForge is gone. Its last line of output is JSON: the files it wrote, or yt-dlp's error.

    python -I -m clipforge.ingest.section_child <ClipForge's pid> <url> <folder> <quality> <start> <end> ...
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from clipforge.errors import ClipForgeError
from clipforge.ingest.ytdlp import YtDlpClient

WATCH_S = 1.0


def main(argv: list[str]) -> int:
    """``argv`` is the url, the folder, the quality and each range's start and end."""
    url, folder, quality, *bounds = argv
    edges = [float(x) for x in bounds]
    ranges = list(zip(edges[::2], edges[1::2], strict=True))
    try:
        paths = YtDlpClient().sections_here(url, Path(folder), ranges, quality)
    except ClipForgeError as err:
        print(json.dumps({"error": err.details or err.message}), flush=True)
        return 2
    print(json.dumps({"paths": [str(p) for p in paths]}), flush=True)
    return 0


def watch(parent: int) -> None:
    """Waits while ClipForge, process ``parent``, runs, then stops this process and all it started."""
    while os.getppid() == parent:
        time.sleep(WATCH_S)
    os.killpg(0, signal.SIGKILL)


if __name__ == "__main__":
    threading.Thread(target=watch, args=(int(sys.argv[1]),), daemon=True).start()
    sys.exit(main(sys.argv[2:]))
