"""AnalyticsDB mixin: track-lifecycle event sessions."""
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

    @staticmethod
    def _event_session_filter_sql(
        camera_id: Optional[int] = None,
        class_name: Optional[str] = None,
        state: Optional[str] = None,
        since: Optional[float] = None,
    ) -> tuple:
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
        return " AND ".join(clauses), params

    def count_event_sessions(
        self,
        camera_id: Optional[int] = None,
        class_name: Optional[str] = None,
        state: Optional[str] = None,
        since: Optional[float] = None,
    ) -> int:
        where, params = self._event_session_filter_sql(
            camera_id=camera_id, class_name=class_name, state=state, since=since,
        )
        sql = f"SELECT COUNT(*) FROM event_sessions WHERE {where}"
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def list_event_sessions(
        self,
        camera_id: Optional[int] = None,
        class_name: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        since: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        where, params = self._event_session_filter_sql(
            camera_id=camera_id, class_name=class_name, state=state, since=since,
        )
        params = list(params)
        params.extend([limit, offset])
        sql = f"""
            SELECT id, camera_id, class_name, track_id, started_at, updated_at, ended_at,
                   state, max_confidence, snapshot_path, clip_path, region_labels, plate_text
            FROM event_sessions
            WHERE {where}
            ORDER BY started_at DESC
            LIMIT ? OFFSET ?
        """
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
        out = []
        for r in rows:
            out.append(self._event_session_row(r))
        return out

    def get_event_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT id, camera_id, class_name, track_id, started_at, updated_at, ended_at,
                       state, max_confidence, snapshot_path, clip_path, region_labels, plate_text
                FROM event_sessions WHERE id = ?
                """,
                (session_id,),
            )
            r = cur.fetchone()
        return self._event_session_row(r) if r else None

    @staticmethod
    def _event_session_row(r) -> Dict[str, Any]:
        return {
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
        }

    @staticmethod
    def _unix_to_sql_ts(ts: float) -> str:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S+00:00"
        )

    def get_nearby_detections_for_session(
        self,
        session_id: str,
        window_s: float = 10.0,
        same_class: bool = True,
        limit: int = 80,
    ) -> Dict[str, Any]:
        """Detections near an event session for GIF / frame playback.

        Prefers same camera + track_id when available; otherwise same camera
        (+ class when ``same_class``). Ordered by time ascending.
        """
        session = self.get_event_session(session_id)
        if not session:
            return {"session": None, "detections": [], "mode": None, "window_s": window_s}

        window_s = max(1.0, min(float(window_s), 120.0))
        limit = max(2, min(int(limit), 200))
        center = float(session.get("started_at") or 0)
        end = session.get("ended_at") or session.get("updated_at") or center
        t_lo = min(center, float(end)) - window_s
        t_hi = max(center, float(end)) + window_s
        sql_lo = self._unix_to_sql_ts(t_lo)
        sql_hi = self._unix_to_sql_ts(t_hi)
        cam = int(session["camera_id"])
        track_id = session.get("track_id")
        class_name = session.get("class_name")

        def _query(extra_clauses: list, extra_params: list) -> List[Dict[str, Any]]:
            where = [
                "camera_id = ?",
                "timestamp >= ?",
                "timestamp <= ?",
                "training_label IS NULL",
            ] + extra_clauses
            sql = f"""
                SELECT id, camera_id, timestamp, class_name, confidence,
                       bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id,
                       thumbnail_path, full_image_path
                FROM events
                WHERE {' AND '.join(where)}
                ORDER BY timestamp ASC
                LIMIT ?
            """
            params = [cam, sql_lo, sql_hi] + list(extra_params) + [limit]
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            out = []
            for row in rows:
                out.append({
                    "id": row[0],
                    "camera_id": row[1],
                    "timestamp": row[2],
                    "class_name": row[3],
                    "confidence": round(row[4], 3) if row[4] else 0,
                    "bbox": [row[5], row[6], row[7], row[8]],
                    "track_id": row[9],
                    "thumbnail_path": row[10],
                    "full_image_path": row[11],
                })
            return out

        mode = "camera"
        detections: List[Dict[str, Any]] = []
        if track_id is not None:
            detections = _query(["track_id = ?"], [int(track_id)])
            if len(detections) >= 2:
                mode = "track"
            else:
                detections = []

        if not detections:
            if same_class and class_name:
                detections = _query(["class_name = ?"], [class_name])
                mode = "camera_class"
            else:
                detections = _query([], [])
                mode = "camera"

        if hasattr(self, "_filter_resolvable_detections"):
            detections = self._filter_resolvable_detections(detections)  # type: ignore[attr-defined]

        return {
            "session": session,
            "detections": detections,
            "mode": mode,
            "window_s": window_s,
            "t_lo": t_lo,
            "t_hi": t_hi,
        }

    def find_adjacent_clip_sessions(
        self,
        session_id: str,
        gap_s: float = 15.0,
        prefer_same_class: bool = True,
    ) -> List[Dict[str, Any]]:
        """Sessions with clips that overlap or sit within ``gap_s`` of the seed.

        Prefers same camera + class; falls back to same camera. Returns the
        connected cluster sorted by started_at (includes the seed when it has a clip).
        """
        from basebuddy.modules.config import EVENT_CLIP_PRE_S, EVENT_CLIP_POST_S

        seed = self.get_event_session(session_id)
        if not seed:
            return []

        gap_s = max(0.0, min(float(gap_s), 300.0))
        pre = float(EVENT_CLIP_PRE_S)
        post = float(EVENT_CLIP_POST_S)
        cam = int(seed["camera_id"])
        seed_start = float(seed["started_at"] or 0)
        # Look in a wide window so we can cluster
        lookback = 3600.0
        since = seed_start - lookback
        until = seed_start + lookback

        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT id, camera_id, class_name, track_id, started_at, updated_at, ended_at,
                       state, max_confidence, snapshot_path, clip_path, region_labels, plate_text
                FROM event_sessions
                WHERE camera_id = ?
                  AND clip_path IS NOT NULL AND TRIM(clip_path) != ''
                  AND started_at >= ? AND started_at <= ?
                ORDER BY started_at ASC
                """,
                (cam, since, until),
            )
            rows = [self._event_session_row(r) for r in cur.fetchall()]

        if prefer_same_class and seed.get("class_name"):
            same = [r for r in rows if r.get("class_name") == seed["class_name"]]
            if any(r["id"] == session_id for r in same) or seed.get("clip_path"):
                rows = same if same else rows

        def _range(s: Dict[str, Any]):
            t0 = float(s["started_at"] or 0) - pre
            t1 = float(s.get("ended_at") or s.get("updated_at") or s["started_at"] or 0) + post
            return t0, max(t1, t0 + 0.1)

        # Ensure seed is in the candidate set even without a clip (for UI context)
        by_id = {r["id"]: r for r in rows}
        if session_id not in by_id and seed.get("clip_path"):
            by_id[session_id] = seed
            rows = sorted(by_id.values(), key=lambda s: float(s["started_at"] or 0))

        if session_id not in by_id:
            # Seed has no clip; still return nearby clip sessions that touch its window
            seed_t0, seed_t1 = _range(seed)
            nearby = []
            for r in rows:
                a0, a1 = _range(r)
                if a0 <= seed_t1 + gap_s and a1 >= seed_t0 - gap_s:
                    nearby.append(r)
            return nearby

        # BFS cluster by adjacency / overlap
        ranges = {r["id"]: _range(r) for r in rows}
        cluster_ids = {session_id}
        changed = True
        while changed:
            changed = False
            for rid, (a0, a1) in ranges.items():
                if rid in cluster_ids:
                    continue
                for cid in list(cluster_ids):
                    b0, b1 = ranges[cid]
                    if a0 <= b1 + gap_s and a1 >= b0 - gap_s:
                        cluster_ids.add(rid)
                        changed = True
                        break

        cluster = [by_id[i] for i in cluster_ids if i in by_id]
        cluster.sort(key=lambda s: float(s["started_at"] or 0))
        for s in cluster:
            t0, t1 = _range(s)
            s["clip_t0"] = t0
            s["clip_t1"] = t1
        return cluster
