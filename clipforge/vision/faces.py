"""Faces (SPEC 7.6): OpenCV's YuNet 2023mar on every analysis frame, score threshold 0.6, NMS 0.3."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from clipforge.models import FaceBox

SCORE = 0.6
NMS = 0.3
TOP_K = 50


class FaceDetector:
    def __init__(
        self,
        model: Path | None,
        *,
        score: float = SCORE,
        nms: float = NMS,
        top_k: int = TOP_K,
        net: Any = None,
    ):
        """``net`` stands in for YuNet in tests."""
        cv2.setNumThreads(2)
        if net is None:
            net = cv2.FaceDetectorYN.create(str(model), "", (640, 360), score, nms, top_k)
        self._net = net
        self._size: tuple[int, int] | None = None

    def detect(self, frame: np.ndarray, t: float) -> list[FaceBox]:
        h, w = frame.shape[:2]
        if self._size != (w, h):
            self._net.setInputSize((w, h))
            self._size = (w, h)
        _, rows = self._net.detect(frame)
        if rows is None:
            return []
        found = []
        for row in rows:
            x0, y0 = max(0.0, float(row[0])), max(0.0, float(row[1]))
            x1, y1 = min(float(w), float(row[0] + row[2])), min(float(h), float(row[1] + row[3]))
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            marks = [(float(row[4 + 2 * k]) / w, float(row[5 + 2 * k]) / h) for k in range(5)]
            found.append(
                FaceBox(
                    t=t,
                    x=x0 / w,
                    y=y0 / h,
                    w=(x1 - x0) / w,
                    h=(y1 - y0) / h,
                    score=float(row[14]),
                    marks=marks,
                )
            )
        return found
