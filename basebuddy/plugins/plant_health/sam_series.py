"""Versioned SAM point-set / growth series persistence."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from basebuddy.plugins.plant_health.db import _db, _ensure_columns, init_plant_tables

_SAMPLE_COLS = (
    "id, series_id, monitor_id, sampled_at, "
    "mask_area_px, mask_area_fraction, "
    "green_pixels_in_mask, total_mask_pixels, green_fraction_in_mask, "
    "frame_w, frame_h, exemplar_path, quality_flags, ok, error, "
    "mask_path, metrics_json, silhouette_path, raw_path"
)


def ensure_sam_growth_tables(conn) -> None:
    """Create / migrate SAM growth tables (safe for existing DBs)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plant_sam_series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitor_id TEXT NOT NULL,
            camera_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            label TEXT,
            points_json TEXT NOT NULL,
            labels_json TEXT NOT NULL,
            frame_w INTEGER,
            frame_h INTEGER,
            initial_mask_path TEXT,
            active INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_sam_series_monitor "
        "ON plant_sam_series(monitor_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_sam_series_active "
        "ON plant_sam_series(monitor_id, active)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plant_sam_growth_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            monitor_id TEXT NOT NULL,
            sampled_at REAL NOT NULL,
            mask_area_px INTEGER,
            mask_area_fraction REAL,
            green_pixels_in_mask INTEGER,
            total_mask_pixels INTEGER,
            green_fraction_in_mask REAL,
            frame_w INTEGER,
            frame_h INTEGER,
            exemplar_path TEXT,
            quality_flags TEXT,
            ok INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            metrics_json TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_sam_growth_series "
        "ON plant_sam_growth_samples(series_id, sampled_at ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_sam_growth_monitor "
        "ON plant_sam_growth_samples(monitor_id, sampled_at DESC)"
    )
    _ensure_columns(
        conn,
        "plant_sam_growth_samples",
        (
            ("metrics_json", "TEXT"),
            ("mask_path", "TEXT"),
            ("silhouette_path", "TEXT"),
            ("raw_path", "TEXT"),
        ),
    )


def create_series(
    monitor_id: str,
    camera_id: int,
    points: List,
    labels: List,
    *,
    frame_w: Optional[int] = None,
    frame_h: Optional[int] = None,
    initial_mask_path: Optional[str] = None,
    label: Optional[str] = None,
    activate: bool = True,
) -> dict:
    """Create a new growth series; optionally deactivate prior active series."""
    db = _db()
    now = time.time()
    if not label:
        label = time.strftime("Series %Y-%m-%d %H:%M", time.localtime(now))
    with db._connect() as conn:
        init_plant_tables(conn)
        ensure_sam_growth_tables(conn)
        if activate:
            conn.execute(
                "UPDATE plant_sam_series SET active = 0 WHERE monitor_id = ? AND active = 1",
                (monitor_id,),
            )
        cur = conn.execute(
            """
            INSERT INTO plant_sam_series (
                monitor_id, camera_id, created_at, label,
                points_json, labels_json, frame_w, frame_h,
                initial_mask_path, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id,
                int(camera_id),
                now,
                label,
                json.dumps(points, default=float),
                json.dumps([int(x) for x in labels]),
                frame_w,
                frame_h,
                initial_mask_path,
                1 if activate else 0,
            ),
        )
        conn.commit()
        sid = int(cur.lastrowid)
    return get_series(sid) or {"id": sid, "monitor_id": monitor_id}


def update_series_anchors(
    series_id: int,
    points: List,
    labels: List,
    *,
    frame_w: Optional[int] = None,
    frame_h: Optional[int] = None,
    initial_mask_path: Optional[str] = None,
) -> Optional[dict]:
    """Update SAM anchors on an existing series without starting a new one."""
    series = get_series(series_id)
    if not series:
        return None
    db = _db()
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        conn.execute(
            """
            UPDATE plant_sam_series
            SET points_json = ?, labels_json = ?,
                frame_w = COALESCE(?, frame_w),
                frame_h = COALESCE(?, frame_h),
                initial_mask_path = COALESCE(?, initial_mask_path)
            WHERE id = ?
            """,
            (
                json.dumps(points, default=float),
                json.dumps([int(x) for x in labels]),
                frame_w,
                frame_h,
                initial_mask_path,
                int(series_id),
            ),
        )
        conn.commit()
    return get_series(series_id)


def set_series_label(series_id: int, label: str) -> Optional[dict]:
    series = get_series(series_id)
    if not series:
        return None
    db = _db()
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        conn.execute(
            "UPDATE plant_sam_series SET label = ? WHERE id = ?",
            (str(label or "").strip() or series.get("label"), int(series_id)),
        )
        conn.commit()
    return get_series(series_id)


def activate_series(series_id: int) -> Optional[dict]:
    """Make *series_id* the sole active series for its monitor."""
    series = get_series(series_id)
    if not series:
        return None
    db = _db()
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        conn.execute(
            "UPDATE plant_sam_series SET active = 0 WHERE monitor_id = ? AND active = 1",
            (series["monitor_id"],),
        )
        conn.execute(
            "UPDATE plant_sam_series SET active = 1 WHERE id = ?",
            (series_id,),
        )
        conn.commit()
    return get_series(series_id)


def get_series(series_id: int) -> Optional[dict]:
    db = _db()
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        cur = conn.execute(
            """
            SELECT id, monitor_id, camera_id, created_at, label,
                   points_json, labels_json, frame_w, frame_h,
                   initial_mask_path, active
            FROM plant_sam_series WHERE id = ?
            """,
            (int(series_id),),
        )
        row = cur.fetchone()
    return _row_to_series(row) if row else None


def get_active_series(monitor_id: str) -> Optional[dict]:
    db = _db()
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        cur = conn.execute(
            """
            SELECT id, monitor_id, camera_id, created_at, label,
                   points_json, labels_json, frame_w, frame_h,
                   initial_mask_path, active
            FROM plant_sam_series
            WHERE monitor_id = ? AND active = 1
            ORDER BY created_at DESC LIMIT 1
            """,
            (monitor_id,),
        )
        row = cur.fetchone()
    return _row_to_series(row) if row else None


def list_series(monitor_id: str, limit: int = 50) -> List[dict]:
    db = _db()
    limit = max(1, int(limit))
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        cur = conn.execute(
            """
            SELECT id, monitor_id, camera_id, created_at, label,
                   points_json, labels_json, frame_w, frame_h,
                   initial_mask_path, active
            FROM plant_sam_series
            WHERE monitor_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (monitor_id, limit),
        )
        rows = cur.fetchall()
        # Attach sample counts in one pass
        out = []
        for row in rows:
            s = _row_to_series(row)
            cur2 = conn.execute(
                "SELECT COUNT(*), MAX(sampled_at), MIN(sampled_at) "
                "FROM plant_sam_growth_samples WHERE series_id = ? AND ok = 1",
                (s["id"],),
            )
            c = cur2.fetchone()
            s["sample_count"] = int(c[0]) if c else 0
            s["last_sampled_at"] = float(c[1]) if c and c[1] is not None else None
            s["first_sampled_at"] = float(c[2]) if c and c[2] is not None else None
            out.append(s)
    return out


def save_growth_sample(
    series_id: int,
    monitor_id: str,
    metrics: Dict[str, Any],
    *,
    exemplar_path: Optional[str] = None,
    mask_path: Optional[str] = None,
    silhouette_path: Optional[str] = None,
    raw_path: Optional[str] = None,
    ok: bool = True,
    error: Optional[str] = None,
    sampled_at: Optional[float] = None,
) -> int:
    db = _db()
    ts = float(sampled_at) if sampled_at is not None else time.time()
    flags = metrics.get("quality_flags") or []
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO plant_sam_growth_samples (
                series_id, monitor_id, sampled_at,
                mask_area_px, mask_area_fraction,
                green_pixels_in_mask, total_mask_pixels, green_fraction_in_mask,
                frame_w, frame_h, exemplar_path, quality_flags, ok, error,
                metrics_json, mask_path, silhouette_path, raw_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(series_id),
                monitor_id,
                ts,
                metrics.get("mask_area_px"),
                metrics.get("mask_area_fraction"),
                metrics.get("green_pixels_in_mask"),
                metrics.get("total_mask_pixels"),
                metrics.get("green_fraction_in_mask"),
                metrics.get("frame_w"),
                metrics.get("frame_h"),
                exemplar_path,
                json.dumps(flags),
                1 if ok else 0,
                error,
                json.dumps(metrics, default=str),
                mask_path,
                silhouette_path,
                raw_path,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_growth_sample(
    sample_id: int,
    metrics: Dict[str, Any],
    *,
    mask_path: Optional[str] = None,
    silhouette_path: Optional[str] = None,
    raw_path: Optional[str] = None,
    ok: bool = True,
    error: Optional[str] = None,
) -> None:
    flags = metrics.get("quality_flags") or []
    db = _db()
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        conn.execute(
            """
            UPDATE plant_sam_growth_samples SET
                mask_area_px = ?, mask_area_fraction = ?,
                green_pixels_in_mask = ?, total_mask_pixels = ?,
                green_fraction_in_mask = ?, frame_w = ?, frame_h = ?,
                quality_flags = ?, ok = ?, error = ?, metrics_json = ?,
                mask_path = COALESCE(?, mask_path),
                silhouette_path = COALESCE(?, silhouette_path),
                raw_path = COALESCE(?, raw_path)
            WHERE id = ?
            """,
            (
                metrics.get("mask_area_px"),
                metrics.get("mask_area_fraction"),
                metrics.get("green_pixels_in_mask"),
                metrics.get("total_mask_pixels"),
                metrics.get("green_fraction_in_mask"),
                metrics.get("frame_w"),
                metrics.get("frame_h"),
                json.dumps(flags),
                1 if ok else 0,
                error,
                json.dumps(metrics, default=str),
                mask_path,
                silhouette_path,
                raw_path,
                int(sample_id),
            ),
        )
        conn.commit()


SAMPLE_QUERY_CAP = 100_000


def sample_window(args, *, now: float) -> tuple:
    """Return ``(limit, since)`` for a growth playback query.

    ``hours`` is a lookback from *now*. ``since`` is an absolute timestamp.
    Limit is capped only to keep one response from loading an unbounded table.
    """
    raw_limit = None if args is None else args.get("limit")
    try:
        limit = int(raw_limit) if raw_limit not in (None, "") else SAMPLE_QUERY_CAP
    except (TypeError, ValueError):
        limit = SAMPLE_QUERY_CAP
    limit = max(1, min(int(limit), SAMPLE_QUERY_CAP))
    since = None
    raw_since = None if args is None else args.get("since")
    raw_hours = None if args is None else args.get("hours")
    if raw_since not in (None, ""):
        try:
            since = float(raw_since)
        except (TypeError, ValueError):
            since = None
    elif raw_hours not in (None, ""):
        try:
            hours = float(raw_hours)
            if hours > 0:
                since = float(now) - hours * 3600.0
        except (TypeError, ValueError):
            since = None
    return limit, since


def list_growth_samples(
    series_id: int,
    *,
    limit: int = 500,
    since: Optional[float] = None,
) -> List[dict]:
    """Newest *limit* samples in chronological order."""
    db = _db()
    limit = max(1, int(limit))
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        if since is not None:
            cur = conn.execute(
                f"SELECT {_SAMPLE_COLS} FROM plant_sam_growth_samples "
                "WHERE series_id = ? AND sampled_at >= ? "
                "ORDER BY sampled_at DESC LIMIT ?",
                (int(series_id), float(since), limit),
            )
        else:
            cur = conn.execute(
                f"SELECT {_SAMPLE_COLS} FROM plant_sam_growth_samples "
                "WHERE series_id = ? ORDER BY sampled_at DESC LIMIT ?",
                (int(series_id), limit),
            )
        rows = list(reversed(cur.fetchall()))
    return [_row_to_sample(r) for r in rows]


def list_exemplars(series_id: int, limit: int = 60) -> List[dict]:
    """Samples that have an exemplar still, chronological."""
    db = _db()
    limit = max(1, int(limit))
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        cur = conn.execute(
            f"SELECT {_SAMPLE_COLS} FROM plant_sam_growth_samples "
            "WHERE series_id = ? AND exemplar_path IS NOT NULL AND exemplar_path != '' "
            "ORDER BY sampled_at DESC LIMIT ?",
            (int(series_id), limit),
        )
        rows = list(reversed(cur.fetchall()))
    return [_row_to_sample(r) for r in rows]


def latest_growth_sample_ts(monitor_id: str, *, series_id: Optional[int] = None) -> Optional[float]:
    db = _db()
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        if series_id is not None:
            cur = conn.execute(
                "SELECT sampled_at FROM plant_sam_growth_samples "
                "WHERE series_id = ? ORDER BY sampled_at DESC LIMIT 1",
                (int(series_id),),
            )
        else:
            cur = conn.execute(
                "SELECT sampled_at FROM plant_sam_growth_samples "
                "WHERE monitor_id = ? ORDER BY sampled_at DESC LIMIT 1",
                (monitor_id,),
            )
        row = cur.fetchone()
    return float(row[0]) if row else None


def latest_exemplar_ts(series_id: int) -> Optional[float]:
    db = _db()
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        cur = conn.execute(
            "SELECT sampled_at FROM plant_sam_growth_samples "
            "WHERE series_id = ? AND exemplar_path IS NOT NULL AND exemplar_path != '' "
            "ORDER BY sampled_at DESC LIMIT 1",
            (int(series_id),),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


def previous_ok_sample(series_id: int) -> Optional[dict]:
    db = _db()
    with db._connect() as conn:
        ensure_sam_growth_tables(conn)
        cur = conn.execute(
            f"SELECT {_SAMPLE_COLS} FROM plant_sam_growth_samples "
            "WHERE series_id = ? AND ok = 1 ORDER BY sampled_at DESC LIMIT 1",
            (int(series_id),),
        )
        row = cur.fetchone()
    return _row_to_sample(row) if row else None


def _row_to_series(row) -> dict:
    try:
        points = json.loads(row[5]) if row[5] else []
    except json.JSONDecodeError:
        points = []
    try:
        labels = json.loads(row[6]) if row[6] else []
    except json.JSONDecodeError:
        labels = []
    return {
        "id": row[0],
        "monitor_id": row[1],
        "camera_id": row[2],
        "created_at": row[3],
        "label": row[4],
        "points": points,
        "labels": labels,
        "frame_w": row[7],
        "frame_h": row[8],
        "initial_mask_path": row[9],
        "active": bool(row[10]),
    }


def _row_to_sample(row) -> dict:
    try:
        flags = json.loads(row[12]) if row[12] else []
    except (json.JSONDecodeError, TypeError):
        flags = []
    mask_path = row[15] if len(row) > 15 else None
    metrics: Dict[str, Any] = {}
    if len(row) > 16 and row[16]:
        try:
            parsed = json.loads(row[16])
            if isinstance(parsed, dict):
                metrics = parsed
        except (json.JSONDecodeError, TypeError):
            metrics = {}
    silhouette_path = row[17] if len(row) > 17 else None
    raw_path = row[18] if len(row) > 18 else None
    L_frame = metrics.get("L_frame")
    L_plant = metrics.get("L_plant")
    lights_out = _sample_is_lights_out(flags, row[6], row[8], L_plant)
    return {
        "id": row[0],
        "series_id": row[1],
        "monitor_id": row[2],
        "sampled_at": row[3],
        "mask_area_px": row[4],
        "mask_area_fraction": row[5],
        "green_pixels_in_mask": row[6],
        "total_mask_pixels": row[7],
        "green_fraction_in_mask": row[8],
        "frame_w": row[9],
        "frame_h": row[10],
        "exemplar_path": row[11],
        "quality_flags": flags,
        "ok": bool(row[13]),
        "error": row[14],
        "mask_path": mask_path,
        "silhouette_path": silhouette_path,
        "raw_path": raw_path or row[11],
        "L_frame": L_frame,
        "L_plant": L_plant,
        "lights_out": lights_out,
    }


def _sample_is_lights_out(
    flags: List,
    green_px: Any,
    green_frac: Any,
    L_plant: Any,
) -> bool:
    """True when grow-lights / night wash make foliage metrics unusable."""
    flagset = {str(f) for f in (flags or [])}
    if "plant_desaturated" in flagset:
        return True
    green_zero = green_px == 0 or green_frac == 0 or green_frac == 0.0
    if "low_green_in_mask" in flagset and green_zero:
        return True
    try:
        if L_plant is not None and float(L_plant) > 0.7 and green_zero:
            return True
    except (TypeError, ValueError):
        pass
    return False
