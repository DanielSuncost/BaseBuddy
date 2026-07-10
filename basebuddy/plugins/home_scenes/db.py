"""Scene state persistence."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _db():
    import basebuddy.modules.state as shared_state
    return shared_state.analytics_db


def init_scene_tables(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_states (
            scene_id TEXT NOT NULL,
            slot_id TEXT NOT NULL,
            state TEXT NOT NULL,
            confidence REAL,
            last_checked_at DATETIME,
            consecutive_empty INTEGER DEFAULT 0,
            PRIMARY KEY (scene_id, slot_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_id TEXT,
            slot_id TEXT,
            event_type TEXT,
            old_state TEXT,
            new_state TEXT,
            snapshot_path TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scene_events_scene ON scene_events(scene_id, created_at)")


def get_slot_state(scene_id: str, slot_id: str) -> Optional[dict]:
    db = _db()
    with db._connect() as conn:
        row = conn.execute(
            "SELECT scene_id, slot_id, state, confidence, last_checked_at, consecutive_empty "
            "FROM scene_states WHERE scene_id = ? AND slot_id = ?",
            (scene_id, slot_id),
        ).fetchone()
    if not row:
        return None
    return {
        "scene_id": row[0],
        "slot_id": row[1],
        "state": row[2],
        "confidence": row[3],
        "last_checked_at": row[4],
        "consecutive_empty": row[5],
    }


def upsert_slot_state(
    scene_id: str,
    slot_id: str,
    state: str,
    confidence: float,
    consecutive_empty: int,
) -> None:
    db = _db()
    now = datetime.now(timezone.utc).isoformat()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO scene_states (scene_id, slot_id, state, confidence, last_checked_at, consecutive_empty)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(scene_id, slot_id) DO UPDATE SET
                state = excluded.state,
                confidence = excluded.confidence,
                last_checked_at = excluded.last_checked_at,
                consecutive_empty = excluded.consecutive_empty
            """,
            (scene_id, slot_id, state, confidence, now, consecutive_empty),
        )


def add_scene_event(
    scene_id: str,
    slot_id: str,
    event_type: str,
    old_state: Optional[str],
    new_state: Optional[str],
    snapshot_path: Optional[str] = None,
) -> None:
    db = _db()
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO scene_events (scene_id, slot_id, event_type, old_state, new_state, snapshot_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (scene_id, slot_id, event_type, old_state, new_state, snapshot_path),
        )


def list_scene_states(scene_id: str) -> List[dict]:
    db = _db()
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT scene_id, slot_id, state, confidence, last_checked_at, consecutive_empty "
            "FROM scene_states WHERE scene_id = ?",
            (scene_id,),
        ).fetchall()
    return [
        {
            "scene_id": r[0],
            "slot_id": r[1],
            "state": r[2],
            "confidence": r[3],
            "last_checked_at": r[4],
            "consecutive_empty": r[5],
        }
        for r in rows
    ]


def list_scene_events(scene_id: str, limit: int = 50) -> List[dict]:
    db = _db()
    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, scene_id, slot_id, event_type, old_state, new_state, snapshot_path, created_at
            FROM scene_events WHERE scene_id = ? ORDER BY created_at DESC LIMIT ?
            """,
            (scene_id, limit),
        ).fetchall()
    return [
        {
            "id": r[0],
            "scene_id": r[1],
            "slot_id": r[2],
            "event_type": r[3],
            "old_state": r[4],
            "new_state": r[5],
            "snapshot_path": r[6],
            "created_at": r[7],
        }
        for r in rows
    ]
