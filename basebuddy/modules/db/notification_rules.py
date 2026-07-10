"""Per-camera/class notification rule storage.

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


class NotificationRulesMixin:
    # ---------------- Notification rules -----------------
    def list_notification_rules(
        self, camera_id: Optional[int] = None, enabled_only: bool = False
    ) -> List[Dict[str, Any]]:
        import json
        clauses: list = []
        params: list = []
        if enabled_only:
            clauses.append("enabled = 1")
        if camera_id is not None:
            clauses.append("(camera_id IS NULL OR camera_id = ?)")
            params.append(camera_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            cur = conn.execute(
                f"SELECT id, enabled, camera_id, class_name, min_confidence, cooldown_s, "
                f"notify_on, channels, include_snapshot, include_clip, label, created_at "
                f"FROM notification_rules{where} ORDER BY camera_id, class_name",
                params,
            )
            rows = cur.fetchall()
        out = []
        for r in rows:
            try:
                channels = json.loads(r[7]) if r[7] else []
            except Exception:
                channels = []
            out.append({
                "id": r[0],
                "enabled": bool(r[1]),
                "camera_id": r[2],
                "class_name": r[3],
                "min_confidence": r[4],
                "cooldown_s": r[5],
                "notify_on": r[6],
                "channels": channels,
                "include_snapshot": bool(r[8]),
                "include_clip": bool(r[9]),
                "label": r[10],
                "created_at": r[11],
            })
        return out

    def upsert_notification_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        import json
        import time as _time
        channels = rule.get("channels") or []
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",") if c.strip()]
        rid = rule.get("id")
        values = (
            1 if rule.get("enabled", True) else 0,
            rule.get("camera_id"),
            (rule.get("class_name") or "*").strip(),
            float(rule.get("min_confidence") or 0),
            float(rule.get("cooldown_s") or 60),
            (rule.get("notify_on") or "start").lower(),
            json.dumps(channels),
            1 if rule.get("include_snapshot", True) else 0,
            1 if rule.get("include_clip") else 0,
            rule.get("label") or "",
            rule.get("created_at") or _time.time(),
        )
        with self._connect() as conn:
            if rid:
                conn.execute(
                    """
                    UPDATE notification_rules SET enabled=?, camera_id=?, class_name=?,
                    min_confidence=?, cooldown_s=?, notify_on=?, channels=?,
                    include_snapshot=?, include_clip=?, label=? WHERE id=?
                    """,
                    values[:-1] + (rid,),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO notification_rules (
                        enabled, camera_id, class_name, min_confidence, cooldown_s,
                        notify_on, channels, include_snapshot, include_clip, label, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                rid = cur.lastrowid
            conn.commit()
        rules = self.list_notification_rules()
        return next((r for r in rules if r["id"] == rid), {"id": rid})

    def delete_notification_rule(self, rule_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM notification_rules WHERE id = ?", (rule_id,))
            conn.commit()
            return cur.rowcount > 0
