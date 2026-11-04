"""Answers kept on disk under a hash of the question (SPEC 9.2), so a rerun or a resumed project never
asks the same thing twice."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def cache_key(provider: str, model: str, system: str, prompt: str, schema: dict) -> str:
    """The sha256 of everything that shapes the answer."""
    blob = json.dumps(
        [provider, model, system, prompt, schema], sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class LLMCache:
    def __init__(self, dir: Path):
        self.dir = Path(dir)

    def get(self, key: str) -> dict | None:
        """The answer stored under ``key``; None when there is none or the file can't be read."""
        try:
            data = json.loads((self.dir / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def put(self, key: str, data: dict) -> None:
        """Store ``data``; a reader sees the old file or the new one, never half of one."""
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=f".{key}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, ensure_ascii=False)
            os.replace(tmp, self.dir / f"{key}.json")
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
