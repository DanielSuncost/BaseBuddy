"""
In-process region notification dispatch with cooldown and recent history.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.request
from collections import deque
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_HISTORY = 200
_pending: Deque[dict] = deque(maxlen=50)
_pending_lock = threading.Lock()


class RegionNotificationService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_fired: Dict[tuple, float] = {}
        self._recent: Deque[dict] = deque(maxlen=_MAX_HISTORY)

    def maybe_notify(
        self,
        camera_id: int,
        region: dict,
        class_name: str,
        confidence: float = 0.0,
        track_id: Optional[int] = None,
    ) -> bool:
        notify = region.get('notify') or {}
        if not notify.get('enabled'):
            return False
        classes = notify.get('classes') or []
        if classes and class_name not in classes:
            return False

        region_id = region.get('id') or region.get('label') or 'unknown'
        key = (camera_id, region_id, class_name)
        cooldown = float(notify.get('cooldown_s') or 60)
        now = time.time()

        with self._lock:
            if now - self._last_fired.get(key, 0.0) < cooldown:
                return False
            self._last_fired[key] = now

        event = {
            'ts': now,
            'camera_id': camera_id,
            'region_id': region_id,
            'region_label': region.get('label') or '',
            'class_name': class_name,
            'confidence': confidence,
            'track_id': track_id,
            'message': (
                f"{class_name} detected in region "
                f"\"{region.get('label') or region_id}\" (camera {camera_id + 1})"
            ),
        }
        with self._lock:
            self._recent.appendleft(event)

        logger.info("Region notify: %s", event['message'])

        with _pending_lock:
            _pending.append({
                **event,
                'phase': 'region',
                'webhook_url': notify.get('webhook_url') or region.get('webhook_url'),
            })

        webhook = notify.get('webhook_url') or region.get('webhook_url')
        if webhook:
            self._post_webhook(webhook, event)

        return True

    def flush_pending_with_media(
        self,
        camera_id: int,
        class_name: str,
        thumbnail_path: Optional[str],
        full_image_path: Optional[str],
    ) -> None:
        """Send queued region alerts with detection snapshot attached."""
        to_send = []
        with _pending_lock:
            remain = deque(maxlen=50)
            while _pending:
                item = _pending.popleft()
                if item.get('camera_id') == camera_id and item.get('class_name') == class_name:
                    to_send.append(item)
                else:
                    remain.append(item)
            _pending.extend(remain)

        for item in to_send:
            try:
                from basebuddy.core.services.notification_service import notify_detection
                notify_detection(
                    'region',
                    camera_id,
                    class_name,
                    float(item.get('confidence') or 0),
                    item.get('message') or '',
                    thumbnail_path=thumbnail_path,
                    full_image_path=full_image_path,
                    extra={
                        'region_id': item.get('region_id'),
                        'region_label': item.get('region_label'),
                        'webhook_url': item.get('webhook_url'),
                    },
                )
            except Exception as exc:
                logger.warning("Region notify flush failed: %s", exc)

    def recent(self, limit: int = 50, camera_id: Optional[int] = None) -> List[dict]:
        with self._lock:
            items = list(self._recent)
        if camera_id is not None:
            items = [e for e in items if e.get('camera_id') == camera_id]
        return items[:limit]

    @staticmethod
    def _post_webhook(url: str, payload: dict) -> None:
        try:
            import json
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url,
                data=data,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:
            logger.warning("Region webhook failed: %s", exc)


_service: Optional[RegionNotificationService] = None


def get_region_notification_service() -> RegionNotificationService:
    global _service
    if _service is None:
        _service = RegionNotificationService()
    return _service
