"""
Jersey number OCR using PaddleOCR.
Maps bounding-box regions near detected players → jersey number strings.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def read_jersey_numbers(frame: np.ndarray, person_boxes: list[list[float]]) -> list[str | None]:
    """
    Given a frame and a list of person bounding boxes [x1, y1, x2, y2],
    attempt to read the jersey number from each person's torso region.

    Returns a list of strings (or None if OCR found nothing).
    """
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        logger.warning("PaddleOCR not available — skipping jersey OCR")
        return [None] * len(person_boxes)

    ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
    results = []

    for box in person_boxes:
        x1, y1, x2, y2 = [int(v) for v in box]
        h = y2 - y1
        # Torso region: middle third of the bounding box
        torso_y1 = y1 + h // 3
        torso_y2 = y1 + (h * 2) // 3
        torso = frame[torso_y1:torso_y2, x1:x2]

        if torso.size == 0:
            results.append(None)
            continue

        try:
            ocr_result = ocr.ocr(torso, cls=False)
            numbers = []
            for line in (ocr_result or [[]]):
                for item in (line or []):
                    text, conf = item[1]
                    if conf > 0.5 and text.strip().isdigit():
                        numbers.append(text.strip())
            results.append(numbers[0] if numbers else None)
        except Exception:
            logger.exception("OCR failed for a person region")
            results.append(None)

    return results
