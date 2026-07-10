"""
Track-based event sessions: start / update / end lifecycle with MQTT + clips.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

Key = Tuple[int, int, str]  # camera_id, track_id, class_name


class EventSessionService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Dict[Key, str] = {}
        # Clip finalization sleeps for the post-roll and then runs ffmpeg; it
        # must not block the per-camera detection thread that ends the track.
        self._finalize_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="event-finalize")

    def on_detection_stored(
        self,
        camera_id: int,
        class_name: str,
        confidence: float,
        track_id: Optional[int],
        thumbnail_path: Optional[str],
        region_labels: Optional[str] = None,
        full_image_path: Optional[str] = None,
    ) -> Optional[str]:
        if track_id is None:
            return self._ephemeral_detection(
                camera_id, class_name, confidence, thumbnail_path, region_labels, full_image_path,
            )

        key: Key = (camera_id, int(track_id), class_name)
        with self._lock:
            session_id = self._active.get(key)

        import basebuddy.modules.state as app_state

        db = app_state.analytics_db
        now = time.time()

        if session_id is None:
            session_id = uuid.uuid4().hex[:16]
            db.create_event_session(
                session_id=session_id,
                camera_id=camera_id,
                class_name=class_name,
                track_id=int(track_id),
                confidence=confidence,
                snapshot_path=full_image_path or thumbnail_path,
                region_labels=region_labels,
                started_at=now,
            )
            with self._lock:
                self._active[key] = session_id
            self._start_clip(camera_id, session_id)
            self._try_lpr(camera_id, class_name, session_id)
            self._publish(camera_id, class_name, "new", session_id, confidence, thumbnail_path, track_id, full_image_path=full_image_path)
            self._notify("start", camera_id, class_name, confidence, session_id, thumbnail_path, full_image_path, track_id)
        else:
            db.update_event_session(
                session_id,
                confidence=confidence,
                snapshot_path=full_image_path or thumbnail_path,
                region_labels=region_labels,
                updated_at=now,
            )
            self._publish(camera_id, class_name, "update", session_id, confidence, thumbnail_path, track_id, full_image_path=full_image_path)

        return session_id

    def on_track_end(self, camera_id: int, track_id: int) -> None:
        with self._lock:
            keys = [k for k in self._active if k[0] == camera_id and k[1] == track_id]
            session_ids = [(k, self._active.pop(k)) for k in keys]

        for key, session_id in session_ids:
            self._finalize_pool.submit(self._finalize_session, camera_id, track_id, key[2], session_id)

    def _finalize_session(self, camera_id: int, track_id: int, class_name: str, session_id: str) -> None:
        try:
            import basebuddy.modules.state as app_state
            from basebuddy.modules.config import EVENT_CLIP_POST_S

            db = app_state.analytics_db
            grabber = app_state.grabbers.get(camera_id)

            clip_path = None
            if grabber and hasattr(grabber, "finalize_event_clip"):
                clip_path = grabber.finalize_event_clip(session_id, post_seconds=float(EVENT_CLIP_POST_S))

            row = db.end_event_session(session_id, clip_path=clip_path)
            if not row:
                return
            snap = row.get("snapshot_path")
            self._publish(
                camera_id, class_name, "end", session_id,
                row.get("max_confidence", 0), snap, track_id, clip_path=clip_path,
            )
            self._notify(
                "end", camera_id, class_name, row.get("max_confidence", 0), session_id,
                snap, snap, track_id, clip_path=clip_path,
            )
        except Exception:
            logger.exception("Event session finalize failed (cam %s, session %s)", camera_id, session_id)

    def _ephemeral_detection(
        self,
        camera_id: int,
        class_name: str,
        confidence: float,
        thumbnail_path: Optional[str],
        region_labels: Optional[str],
        full_image_path: Optional[str] = None,
    ) -> Optional[str]:
        session_id = uuid.uuid4().hex[:12]
        import basebuddy.modules.state as app_state

        app_state.analytics_db.create_event_session(
            session_id=session_id,
            camera_id=camera_id,
            class_name=class_name,
            track_id=None,
            confidence=confidence,
            snapshot_path=full_image_path or thumbnail_path,
            region_labels=region_labels,
            started_at=time.time(),
            ended_at=time.time(),
            state="ended",
        )
        self._publish(camera_id, class_name, "new", session_id, confidence, thumbnail_path, None, full_image_path=full_image_path)
        self._notify("start", camera_id, class_name, confidence, session_id, thumbnail_path, full_image_path, None)
        return session_id

    def _notify(
        self,
        phase: str,
        camera_id: int,
        class_name: str,
        confidence: float,
        session_id: str,
        thumbnail_path: Optional[str],
        full_image_path: Optional[str],
        track_id: Optional[int],
        clip_path: Optional[str] = None,
    ) -> None:
        from basebuddy.core.services.notification_service import notify_detection
        from basebuddy.core.services.media_paths import url_to_filesystem

        payload = self._payload(
            camera_id, class_name, session_id, confidence,
            full_image_path or thumbnail_path, track_id, clip_path,
        )
        notify_detection(
            phase,
            camera_id,
            class_name,
            confidence,
            payload["message"],
            thumbnail_path=thumbnail_path,
            full_image_path=full_image_path,
            clip_path=clip_path,
            snapshot_url=payload.get("snapshot_url"),
            clip_url=payload.get("clip_url"),
            extra=payload,
        )

    def _start_clip(self, camera_id: int, session_id: str) -> None:
        import basebuddy.modules.state as app_state
        grabber = app_state.grabbers.get(camera_id)
        if grabber and hasattr(grabber, "start_event_clip"):
            grabber.start_event_clip(session_id)

    def _try_lpr(self, camera_id: int, class_name: str, session_id: str) -> None:
        try:
            import basebuddy.modules.state as app_state
            grabber = app_state.grabbers.get(camera_id)
            if not grabber:
                return
            frame = bbox = None
            for det in getattr(grabber, "last_detections", []) or []:
                if det.get("class_name") == class_name:
                    bbox = det.get("bbox")
                    break
            with grabber.lock:
                if grabber.frames:
                    frame = grabber.frames[-1].copy()
            if frame is not None and bbox is not None:
                from basebuddy.plugins.lpr import maybe_lpr_for_detection
                maybe_lpr_for_detection(camera_id, class_name, frame, bbox, session_id)
        except Exception:
            pass

    @staticmethod
    def _snapshot_url(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        if path.startswith("http"):
            return path
        p = path.replace("\\", "/")
        for prefix in ("recordings/", "detections/", "stills/", "timelapse_output/", "/media/", "/recordings/"):
            if prefix.startswith("/"):
                if p.startswith(prefix):
                    return p
            elif f"/{prefix}" in p or p.startswith(prefix):
                idx = p.find(prefix)
                return f"/{p[idx:]}" if not p.startswith("/") else p
        return f"/{p.lstrip('/')}"

    def _payload(
        self,
        camera_id: int,
        class_name: str,
        session_id: str,
        confidence: float,
        snapshot_path: Optional[str],
        track_id: Optional[int],
        clip_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = self._snapshot_url(snapshot_path)
        clip_url = self._snapshot_url(clip_path) if clip_path else None
        msg = f"{class_name} detected on camera {camera_id + 1}"
        if track_id is not None:
            msg += f" (track {track_id})"
        return {
            "id": session_id,
            "camera_id": camera_id,
            "camera": f"camera_{camera_id + 1}",
            "label": class_name,
            "class_name": class_name,
            "score": confidence,
            "confidence": confidence,
            "track_id": track_id,
            "snapshot_path": snapshot_path,
            "thumbnail_url": snap,
            "snapshot_url": snap,
            "clip_path": clip_path,
            "clip_url": clip_url,
            "message": msg,
            "title": msg,
        }

    def _publish(
        self,
        camera_id: int,
        class_name: str,
        event_type: str,
        session_id: str,
        confidence: float,
        snapshot_path: Optional[str],
        track_id: Optional[int],
        clip_path: Optional[str] = None,
        full_image_path: Optional[str] = None,
    ) -> None:
        from basebuddy.core.services.mqtt_publisher import publish_event
        payload = self._payload(
            camera_id, class_name, session_id, confidence,
            full_image_path or snapshot_path, track_id, clip_path,
        )
        payload["type"] = event_type
        publish_event(camera_id, class_name, event_type, payload)


_service: Optional[EventSessionService] = None


def get_event_session_service() -> EventSessionService:
    global _service
    if _service is None:
        _service = EventSessionService()
    return _service
