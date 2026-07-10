"""Track-lifecycle event session persistence.

Mixin for :class:`modules.database.AnalyticsDB`. Split out of the original
monolithic database module; methods are verbatim and share the same
connection helpers on the composed class.
"""
import os
import sqlite3
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

from basebuddy.modules.config import (
    MEDIA_BASE_DIR,
    MEDIA_URL_PREFIX,
    DEDUP_ENABLE,
    DEDUP_TIME_WINDOW_S,
    DEDUP_CENTER_PX,
    DEDUP_IOU,
    DEDUP_PHASH_MAX_DIST,
    FALSE_POSITIVE_ZONES_ENABLE,
    FALSE_POSITIVE_ZONE_IOU,
)


class EventSessionsMixin:
    # ---------------- Event sessions (track lifecycle) -----------------
    def create_event_session(
        self,
        session_id: str,
        camera_id: int,
        class_name: str,
        track_id: Optional[int],
        confidence: float,
        snapshot_path: Optional[str],
        region_labels: Optional[str],
        started_at: float,
        ended_at: Optional[float] = None,
        state: str = "active",
        clip_path: Optional[str] = None,
        plate_text: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO event_sessions (
                    id, camera_id, class_name, track_id, started_at, updated_at,
                    ended_at, state, max_confidence, snapshot_path, clip_path,
                    region_labels, plate_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, camera_id, class_name, track_id, started_at, started_at,
                    ended_at, state, confidence, snapshot_path, clip_path,
                    region_labels, plate_text,
                ),
            )
            conn.commit()

    def update_event_session(
        self,
        session_id: str,
        confidence: float,
        snapshot_path: Optional[str],
        region_labels: Optional[str],
        updated_at: float,
        plate_text: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            cur = conn.execute("SELECT max_confidence FROM event_sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
            prev = float(row[0]) if row and row[0] is not None else 0.0
            new_conf = max(prev, confidence)
            if plate_text:
                conn.execute(
                    """
                    UPDATE event_sessions SET updated_at = ?, max_confidence = ?,
                           snapshot_path = COALESCE(?, snapshot_path),
                           region_labels = COALESCE(?, region_labels), plate_text = ?
                    WHERE id = ?
                    """,
                    (updated_at, new_conf, snapshot_path, region_labels, plate_text, session_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE event_sessions SET updated_at = ?, max_confidence = ?,
                           snapshot_path = COALESCE(?, snapshot_path),
                           region_labels = COALESCE(?, region_labels)
                    WHERE id = ?
                    """,
                    (updated_at, new_conf, snapshot_path, region_labels, session_id),
                )
            conn.commit()

    def end_event_session(self, session_id: str, clip_path: Optional[str] = None) -> Optional[dict]:
        import time
        ended = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE event_sessions SET state = 'ended', ended_at = ?, clip_path = COALESCE(?, clip_path)
                WHERE id = ?
                """,
                (ended, clip_path, session_id),
            )
            cur = conn.execute(
                "SELECT id, camera_id, class_name, track_id, max_confidence, snapshot_path, clip_path, plate_text FROM event_sessions WHERE id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            conn.commit()
        if not row:
            return None
        return {
            "id": row[0],
            "camera_id": row[1],
            "class_name": row[2],
            "track_id": row[3],
            "max_confidence": row[4],
            "snapshot_path": row[5],
            "clip_path": row[6],
            "plate_text": row[7],
        }

    def list_event_sessions(
        self,
        camera_id: Optional[int] = None,
        class_name: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        since: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: list = []
        if camera_id is not None:
            clauses.append("camera_id = ?")
            params.append(camera_id)
        if class_name:
            clauses.append("class_name = ?")
            params.append(class_name)
        if state:
            clauses.append("state = ?")
            params.append(state)
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(since)
        params.extend([limit, offset])
        sql = f"""
            SELECT id, camera_id, class_name, track_id, started_at, updated_at, ended_at,
                   state, max_confidence, snapshot_path, clip_path, region_labels, plate_text
            FROM event_sessions
            WHERE {' AND '.join(clauses)}
            ORDER BY started_at DESC
            LIMIT ? OFFSET ?
        """
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r[0],
                "camera_id": r[1],
                "class_name": r[2],
                "track_id": r[3],
                "started_at": r[4],
                "updated_at": r[5],
                "ended_at": r[6],
                "state": r[7],
                "max_confidence": r[8],
                "snapshot_path": r[9],
                "clip_path": r[10],
                "region_labels": r[11],
                "plate_text": r[12],
            })
        return out
