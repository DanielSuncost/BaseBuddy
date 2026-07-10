"""
License plate recognition — optional OCR on vehicle detections.
Requires: pip install pytesseract (+ system tesseract-ocr)
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_PLATE_RE = re.compile(r"[A-Z0-9]{4,8}")


def try_read_plate(frame: np.ndarray, bbox) -> Optional[str]:
    from basebuddy.modules.config import LPR_ENABLED
    if not LPR_ENABLED or frame is None or bbox is None:
        return None
    try:
        import pytesseract
    except ImportError:
        return None
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    # Lower half of vehicle bbox — plates usually bottom
    mid = y1 + (y2 - y1) // 2
    crop = frame[mid:y2, x1:x2]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 11, 17, 17)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    text = pytesseract.image_to_string(thresh, config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    cleaned = re.sub(r"[^A-Z0-9]", "", text.upper())
    m = _PLATE_RE.search(cleaned)
    if m:
        return m.group(0)
    return cleaned[:8] if len(cleaned) >= 4 else None


def maybe_lpr_for_detection(
    camera_id: int,
    class_name: str,
    frame,
    bbox,
    session_id: Optional[str] = None,
) -> Optional[str]:
    from basebuddy.modules.config import LPR_CLASSES
    if class_name not in LPR_CLASSES:
        return None
    plate = try_read_plate(frame, bbox)
    if not plate or not session_id:
        return plate
    try:
        import basebuddy.modules.state as st
        st.analytics_db.update_event_session(
            session_id,
            confidence=0.0,
            snapshot_path=None,
            region_labels=None,
            updated_at=__import__("time").time(),
            plate_text=plate,
        )
        from basebuddy.core.services.mqtt_publisher import publish_event
        publish_event(camera_id, "license_plate", "new", {
            "plate": plate,
            "class_name": class_name,
            "session_id": session_id,
        })
    except Exception as exc:
        logger.debug("LPR store failed: %s", exc)
    return plate
