"""Plant analysis history in SQLite."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


def init_plant_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plant_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitor_id TEXT NOT NULL,
            camera_id INTEGER NOT NULL,
            analyzed_at REAL NOT NULL,
            image_path TEXT,
            health_score INTEGER,
            species_guess TEXT,
            result_json TEXT NOT NULL,
            analyzer TEXT DEFAULT 'oss',
            error TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_analyses_monitor "
        "ON plant_analyses(monitor_id, analyzed_at DESC)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plant_color_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitor_id TEXT NOT NULL,
            camera_id INTEGER NOT NULL,
            sampled_at REAL NOT NULL,
            image_path TEXT,
            coverage REAL,
            plant_pixels INTEGER,
            h_mean REAL, s_mean REAL, v_mean REAL,
            r_mean REAL, g_mean REAL, b_mean REAL,
            greenness REAL,
            dominant_colors_json TEXT,
            metrics_json TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_color_monitor "
        "ON plant_color_samples(monitor_id, sampled_at ASC)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plant_blogger_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            monitor_id TEXT NOT NULL,
            posted_at REAL NOT NULL,
            title TEXT,
            text TEXT,
            image_path TEXT,
            destination TEXT,
            trigger_type TEXT DEFAULT 'scheduled',
            ok INTEGER NOT NULL DEFAULT 1,
            error TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_blogger_channel "
        "ON plant_blogger_posts(channel_id, posted_at DESC)"
    )
    conn.commit()


def _db():
    import basebuddy.modules.state as st
    return st.analytics_db


def save_analysis(
    monitor_id: str,
    camera_id: int,
    result: Dict[str, Any],
    *,
    image_path: Optional[str] = None,
    analyzer: str = "oss",
    error: Optional[str] = None,
) -> int:
    db = _db()
    now = time.time()
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO plant_analyses (
                monitor_id, camera_id, analyzed_at, image_path,
                health_score, species_guess, result_json, analyzer, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id,
                camera_id,
                now,
                image_path,
                result.get("health_score") if isinstance(result.get("health_score"), int) else None,
                result.get("species_guess"),
                json.dumps(result, default=str),
                analyzer,
                error,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_analyses(monitor_id: str, limit: int = 20) -> List[dict]:
    db = _db()
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            """
            SELECT id, monitor_id, camera_id, analyzed_at, image_path,
                   health_score, species_guess, result_json, analyzer, error
            FROM plant_analyses WHERE monitor_id = ?
            ORDER BY analyzed_at DESC LIMIT ?
            """,
            (monitor_id, limit),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        try:
            parsed = json.loads(r[7]) if r[7] else {}
        except json.JSONDecodeError:
            parsed = {}
        out.append({
            "id": r[0],
            "monitor_id": r[1],
            "camera_id": r[2],
            "analyzed_at": r[3],
            "image_path": r[4],
            "health_score": r[5],
            "species_guess": r[6],
            "result": parsed,
            "analyzer": r[8],
            "error": r[9],
        })
    return out


def save_color_sample(
    monitor_id: str,
    camera_id: int,
    metrics: Dict[str, Any],
    *,
    image_path: Optional[str] = None,
) -> int:
    db = _db()
    now = time.time()
    hsv = metrics.get("hsv_mean") or {}
    rgb = metrics.get("rgb_mean") or {}
    dom = json.dumps(metrics.get("dominant_colors_rgb") or [])
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO plant_color_samples (
                monitor_id, camera_id, sampled_at, image_path, coverage, plant_pixels,
                h_mean, s_mean, v_mean, r_mean, g_mean, b_mean, greenness,
                dominant_colors_json, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id, camera_id, now, image_path,
                metrics.get("coverage"), metrics.get("plant_pixels"),
                hsv.get("h"), hsv.get("s"), hsv.get("v"),
                rgb.get("r"), rgb.get("g"), rgb.get("b"),
                metrics.get("greenness"), dom, json.dumps(metrics, default=str),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def latest_color_sample_ts(monitor_id: str) -> Optional[float]:
    db = _db()
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            "SELECT sampled_at FROM plant_color_samples WHERE monitor_id = ? "
            "ORDER BY sampled_at DESC LIMIT 1",
            (monitor_id,),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


def list_color_timeline(monitor_id: str, limit: int = 120) -> List[dict]:
    db = _db()
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            """
            SELECT sampled_at, coverage, h_mean, s_mean, v_mean,
                   r_mean, g_mean, b_mean, greenness, dominant_colors_json, image_path
            FROM plant_color_samples WHERE monitor_id = ?
            ORDER BY sampled_at ASC LIMIT ?
            """,
            (monitor_id, limit),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        try:
            dom = json.loads(r[9]) if r[9] else []
        except json.JSONDecodeError:
            dom = []
        out.append({
            "sampled_at": r[0],
            "coverage": r[1],
            "hsv": {"h": r[2], "s": r[3], "v": r[4]},
            "rgb": {"r": r[5], "g": r[6], "b": r[7]},
            "greenness": r[8],
            "dominant_colors_rgb": dom,
            "image_path": r[10],
        })
    return out


def save_blogger_post(
    channel_id: str,
    monitor_id: str,
    text: str,
    title: str,
    image_path: Optional[str],
    *,
    ok: bool,
    error: Optional[str] = None,
    destination: Optional[str] = None,
    trigger: str = "scheduled",
) -> int:
    db = _db()
    now = time.time()
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO plant_blogger_posts (
                channel_id, monitor_id, posted_at, title, text, image_path,
                destination, trigger_type, ok, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id, monitor_id, now, title, text, image_path,
                destination, trigger, 1 if ok else 0, error,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def latest_blogger_post_ts(channel_id: str) -> Optional[float]:
    db = _db()
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            "SELECT posted_at FROM plant_blogger_posts WHERE channel_id = ? AND ok = 1 "
            "ORDER BY posted_at DESC LIMIT 1",
            (channel_id,),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


def list_blogger_posts(channel_id: Optional[str] = None, limit: int = 30) -> List[dict]:
    db = _db()
    with db._connect() as conn:
        init_plant_tables(conn)
        if channel_id:
            cur = conn.execute(
                """
                SELECT id, channel_id, monitor_id, posted_at, title, text, image_path,
                       destination, trigger_type, ok, error
                FROM plant_blogger_posts WHERE channel_id = ?
                ORDER BY posted_at DESC LIMIT ?
                """,
                (channel_id, limit),
            )
        else:
            cur = conn.execute(
                """
                SELECT id, channel_id, monitor_id, posted_at, title, text, image_path,
                       destination, trigger_type, ok, error
                FROM plant_blogger_posts
                ORDER BY posted_at DESC LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()
    return [{
        "id": r[0],
        "channel_id": r[1],
        "monitor_id": r[2],
        "posted_at": r[3],
        "title": r[4],
        "text": r[5],
        "image_path": r[6],
        "destination": r[7],
        "trigger": r[8],
        "ok": bool(r[9]),
        "error": r[10],
    } for r in rows]
