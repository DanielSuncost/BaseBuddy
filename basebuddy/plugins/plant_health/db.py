"""Plant analysis history in SQLite."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple


_COLOR_SAMPLE_EXTRA_COLS = (
    ("G_rel", "REAL"),
    ("L_bg", "REAL"),
    ("G_norm", "REAL"),
    ("yellow_fraction", "REAL"),
    ("sample_ok", "INTEGER"),
    ("sample_quality", "TEXT"),
    ("L_frame", "REAL"),
    ("L_plant", "REAL"),
    ("quality_flags", "TEXT"),
    ("series_id", "INTEGER"),
)

# Match metrics._L_BG_LOW — sanitize legacy night spikes on read.
_L_BG_LOW_READ = 0.08
_L_G_NORM_MIN_READ = 0.42
_G_NORM_MAX_READ = 5.0
_PLANT_S_MIN_READ = 12.0
_G_NORM_EXCLUDE_FLAGS = (
    "L_bg_low",
    "L_frame_low",
    "L_plant_low",
    "G_norm_clipped",
    "G_norm_dim_light",
    "plant_desaturated",
)


def _ensure_columns(conn, table: str, columns: Tuple[Tuple[str, str], ...]) -> None:
    """Add missing SQLite columns (safe for existing DBs)."""
    existing = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, col_type in columns:
        if name in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
        except sqlite3.OperationalError:
            pass


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
            metrics_json TEXT NOT NULL,
            G_rel REAL,
            L_bg REAL,
            G_norm REAL,
            yellow_fraction REAL,
            sample_ok INTEGER,
            sample_quality TEXT,
            L_frame REAL,
            L_plant REAL,
            quality_flags TEXT
        )
    """)
    _ensure_columns(conn, "plant_color_samples", _COLOR_SAMPLE_EXTRA_COLS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_color_monitor "
        "ON plant_color_samples(monitor_id, sampled_at ASC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_color_series "
        "ON plant_color_samples(monitor_id, series_id, sampled_at ASC)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plant_watering_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitor_id TEXT NOT NULL,
            watered_at REAL NOT NULL,
            note TEXT,
            created_at REAL NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_watering_monitor "
        "ON plant_watering_events(monitor_id, watered_at ASC)"
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
    # SAM growth series (versioned point-sets + area/green samples)
    from basebuddy.plugins.plant_health.sam_series import ensure_sam_growth_tables
    ensure_sam_growth_tables(conn)
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
    sampled_at: Optional[float] = None,
    series_id: Optional[int] = None,
) -> int:
    db = _db()
    now = float(sampled_at) if sampled_at is not None else time.time()
    hsv = metrics.get("hsv_mean") or {}
    rgb = metrics.get("rgb_mean") or {}
    dom = json.dumps(metrics.get("dominant_colors_rgb") or [])
    sample_ok = metrics.get("sample_ok")
    sample_ok_int = None if sample_ok is None else (1 if sample_ok else 0)
    flags = metrics.get("quality_flags") or []
    flags_json = json.dumps(flags) if isinstance(flags, list) else (str(flags) if flags else None)
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO plant_color_samples (
                monitor_id, camera_id, sampled_at, image_path, coverage, plant_pixels,
                h_mean, s_mean, v_mean, r_mean, g_mean, b_mean, greenness,
                dominant_colors_json, metrics_json,
                G_rel, L_bg, G_norm, yellow_fraction, sample_ok, sample_quality,
                L_frame, L_plant, quality_flags, series_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id, camera_id, now, image_path,
                metrics.get("coverage"), metrics.get("plant_pixels"),
                hsv.get("h"), hsv.get("s"), hsv.get("v"),
                rgb.get("r"), rgb.get("g"), rgb.get("b"),
                metrics.get("greenness"), dom, json.dumps(metrics, default=str),
                metrics.get("G_rel"), metrics.get("L_bg"), metrics.get("G_norm"),
                metrics.get("yellow_fraction"), sample_ok_int,
                metrics.get("sample_quality"),
                metrics.get("L_frame"), metrics.get("L_plant"), flags_json,
                int(series_id) if series_id is not None else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_color_sample_for_series(
    monitor_id: str,
    series_id: int,
    sampled_at: float,
    metrics: Dict[str, Any],
) -> bool:
    """Rewrite a reanalyzed color row in place. Leaves the original timeline alone."""
    hsv = metrics.get("hsv_mean") or {}
    rgb = metrics.get("rgb_mean") or {}
    flags = metrics.get("quality_flags") or []
    flags_json = json.dumps(flags) if isinstance(flags, list) else None
    sample_ok = metrics.get("sample_ok")
    sample_ok_int = None if sample_ok is None else (1 if sample_ok else 0)
    db = _db()
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            """
            UPDATE plant_color_samples SET
                coverage = ?, plant_pixels = ?,
                h_mean = ?, s_mean = ?, v_mean = ?,
                r_mean = ?, g_mean = ?, b_mean = ?,
                greenness = ?, metrics_json = ?,
                G_rel = ?, L_bg = ?, G_norm = ?, yellow_fraction = ?,
                sample_ok = ?, sample_quality = ?,
                L_frame = ?, L_plant = ?, quality_flags = ?
            WHERE monitor_id = ? AND series_id = ?
              AND sampled_at BETWEEN ? AND ?
            """,
            (
                metrics.get("coverage"), metrics.get("plant_pixels"),
                hsv.get("h"), hsv.get("s"), hsv.get("v"),
                rgb.get("r"), rgb.get("g"), rgb.get("b"),
                metrics.get("greenness"), json.dumps(metrics, default=str),
                metrics.get("G_rel"), metrics.get("L_bg"), metrics.get("G_norm"),
                metrics.get("yellow_fraction"), sample_ok_int,
                metrics.get("sample_quality"),
                metrics.get("L_frame"), metrics.get("L_plant"), flags_json,
                monitor_id, int(series_id),
                float(sampled_at) - 2.0, float(sampled_at) + 2.0,
            ),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0


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


def _sanitize_night_g_norm(
    G_norm: Optional[float],
    L_bg: Optional[float],
    L_frame: Optional[float],
    sample_ok: Optional[bool],
    sample_quality: Optional[str],
    quality_flags: Optional[List[str]],
    *,
    plant_s_mean: Optional[float] = None,
) -> Tuple[Optional[float], Optional[bool], Optional[str], List[str]]:
    """Drop absurd night / dark-frame G_norm values for legacy rows."""
    flags = list(quality_flags or [])
    lighting_hard = (
        (L_bg is not None and L_bg < _L_BG_LOW_READ)
        or (L_frame is not None and L_frame < _L_BG_LOW_READ)
    )
    dim_for_gnorm = (
        (L_bg is not None and L_bg < _L_G_NORM_MIN_READ)
        or (L_frame is not None and L_frame < _L_G_NORM_MIN_READ)
    )
    desaturated = plant_s_mean is not None and plant_s_mean < _PLANT_S_MIN_READ
    spike = G_norm is not None and (
        lighting_hard or dim_for_gnorm or desaturated or G_norm > _G_NORM_MAX_READ
    )
    if spike and lighting_hard:
        G_norm = None
        if "L_bg_low" not in flags and (L_bg is not None and L_bg < _L_BG_LOW_READ):
            flags.append("L_bg_low")
        if "L_frame_low" not in flags and (L_frame is not None and L_frame < _L_BG_LOW_READ):
            flags.append("L_frame_low")
        sample_ok = False
        sample_quality = "poor"
    elif spike and desaturated:
        G_norm = None
        if "plant_desaturated" not in flags:
            flags.append("plant_desaturated")
        sample_ok = False
        sample_quality = "poor"
    elif spike and dim_for_gnorm:
        # Dim (not pitch-black): drop G_norm but keep sample usable for L_* / coverage.
        G_norm = None
        if "G_norm_dim_light" not in flags:
            flags.append("G_norm_dim_light")
    elif spike and G_norm is not None and G_norm > _G_NORM_MAX_READ:
        # Extreme ratio without recorded L_bg — still exclude from primary trend
        G_norm = None
        if "G_norm_clipped" not in flags:
            flags.append("G_norm_clipped")
        sample_ok = False
        sample_quality = "poor"
    return G_norm, sample_ok, sample_quality, flags


def _row_to_color_sample(r) -> dict:
    try:
        dom = json.loads(r[9]) if r[9] else []
    except json.JSONDecodeError:
        dom = []
    greenness = r[8]
    G_rel = r[12] if len(r) > 12 else None
    if G_rel is None:
        G_rel = greenness  # old rows: greenness was already relative
    L_bg = r[13] if len(r) > 13 else None
    G_norm = r[14] if len(r) > 14 else None
    yellow_fraction = r[15] if len(r) > 15 else None
    sample_ok_raw = r[16] if len(r) > 16 else None
    sample_quality = r[17] if len(r) > 17 else None
    L_frame = r[18] if len(r) > 18 else None
    L_plant = r[19] if len(r) > 19 else None
    flags_raw = r[20] if len(r) > 20 else None
    quality_flags: List[str] = []
    if flags_raw:
        try:
            parsed = json.loads(flags_raw) if isinstance(flags_raw, str) else flags_raw
            if isinstance(parsed, list):
                quality_flags = [str(x) for x in parsed]
        except (json.JSONDecodeError, TypeError):
            quality_flags = []
    if sample_ok_raw is None:
        sample_ok = None
    else:
        sample_ok = bool(sample_ok_raw)
    if sample_quality is None and sample_ok is not None:
        sample_quality = "ok" if sample_ok else "poor"

    plant_s_mean = r[3]  # s_mean column
    G_norm, sample_ok, sample_quality, quality_flags = _sanitize_night_g_norm(
        G_norm,
        L_bg,
        L_frame,
        sample_ok,
        sample_quality,
        quality_flags,
        plant_s_mean=float(plant_s_mean) if plant_s_mean is not None else None,
    )

    return {
        "sampled_at": r[0],
        "coverage": r[1],
        "hsv": {"h": r[2], "s": r[3], "v": r[4]},
        "rgb": {"r": r[5], "g": r[6], "b": r[7]},
        "greenness": greenness,
        "G_raw": greenness,  # legacy greenness alias
        "G_rel": G_rel,
        "L_bg": L_bg,
        "L_frame": L_frame,
        "L_plant": L_plant,
        "G_norm": G_norm,
        "yellow_fraction": yellow_fraction,
        "sample_ok": sample_ok,
        "sample_quality": sample_quality,
        "quality_flags": quality_flags,
        "dominant_colors_rgb": dom,
        "image_path": r[10],
        "plant_pixels": r[11] if len(r) > 11 else None,
    }


_COLOR_SELECT = """
    SELECT sampled_at, coverage, h_mean, s_mean, v_mean,
           r_mean, g_mean, b_mean, greenness, dominant_colors_json, image_path,
           plant_pixels, G_rel, L_bg, G_norm, yellow_fraction, sample_ok, sample_quality,
           L_frame, L_plant, quality_flags
    FROM plant_color_samples
"""


def count_color_samples(monitor_id: str) -> int:
    db = _db()
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            "SELECT COUNT(*) FROM plant_color_samples "
            "WHERE monitor_id = ? AND series_id IS NULL",
            (monitor_id,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else 0


def latest_color_sample(monitor_id: str) -> Optional[dict]:
    rows = list_color_timeline(monitor_id, limit=1)
    return rows[-1] if rows else None


def list_color_source_stills(monitor_id: str) -> List[dict]:
    """Original live-timeline stills that still have an image path."""
    db = _db()
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            "SELECT sampled_at, image_path FROM plant_color_samples "
            "WHERE monitor_id = ? AND series_id IS NULL "
            "AND image_path IS NOT NULL AND image_path != '' "
            "ORDER BY sampled_at ASC",
            (monitor_id,),
        )
        return [{"sampled_at": float(r[0]), "image_path": r[1]} for r in cur.fetchall()]


def list_color_datasets(monitor_id: str) -> List[dict]:
    """Original timeline plus any reanalyzed series that have color rows."""
    db = _db()
    out: List[dict] = []
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            "SELECT series_id, COUNT(*), MIN(sampled_at), MAX(sampled_at) "
            "FROM plant_color_samples WHERE monitor_id = ? "
            "GROUP BY series_id ORDER BY MIN(sampled_at) ASC",
            (monitor_id,),
        )
        rows = cur.fetchall()
        labels = {}
        try:
            from basebuddy.plugins.plant_health.sam_series import get_series

            for row in rows:
                if row[0] is not None:
                    series = get_series(int(row[0]))
                    if series:
                        labels[int(row[0])] = series.get("label")
        except Exception:
            labels = {}
        for row in rows:
            sid = None if row[0] is None else int(row[0])
            out.append({
                "series_id": sid,
                "label": "Original timeline" if sid is None else (labels.get(sid) or f"Series {sid}"),
                "sample_count": int(row[1] or 0),
                "first_sampled_at": float(row[2]) if row[2] is not None else None,
                "last_sampled_at": float(row[3]) if row[3] is not None else None,
            })
    return out


def list_color_timeline(
    monitor_id: str,
    limit: int = 120,
    *,
    since: Optional[float] = None,
    series_id: Optional[int] = None,
) -> List[dict]:
    """Return the newest *limit* samples in chronological (ASC) order.

    Previously used ``ORDER BY sampled_at ASC LIMIT``, which returned the
    *oldest* N rows — charts then ended on a stale date (e.g. 7/12) even when
    newer samples existed.
    """
    db = _db()
    limit = max(1, int(limit))
    series_clause = (
        " AND series_id = ?" if series_id is not None else " AND series_id IS NULL"
    )
    series_args: Tuple = (int(series_id),) if series_id is not None else ()
    with db._connect() as conn:
        init_plant_tables(conn)
        if since is not None:
            cur = conn.execute(
                _COLOR_SELECT
                + " WHERE monitor_id = ? AND sampled_at >= ?"
                + series_clause
                + " ORDER BY sampled_at DESC LIMIT ?",
                (monitor_id, float(since)) + series_args + (limit,),
            )
        else:
            cur = conn.execute(
                _COLOR_SELECT
                + " WHERE monitor_id = ?"
                + series_clause
                + " ORDER BY sampled_at DESC LIMIT ?",
                (monitor_id,) + series_args + (limit,),
            )
        rows = cur.fetchall()
    # Newest-first from SQL → chronological for charts
    return [_row_to_color_sample(r) for r in reversed(rows)]


def _sample_is_good(s: dict) -> bool:
    """Prefer explicit sample_ok; dark / spike frames never drive G_norm trends."""
    ok = s.get("sample_ok")
    if ok is False:
        return False
    # Night / dark / dim / desaturated frames must never drive the primary G_norm trend
    flags = s.get("quality_flags") or []
    if any(f in flags for f in _G_NORM_EXCLUDE_FLAGS):
        return False
    L_bg = s.get("L_bg")
    if L_bg is not None and L_bg < _L_G_NORM_MIN_READ:
        return False
    L_frame = s.get("L_frame")
    if L_frame is not None and L_frame < _L_G_NORM_MIN_READ:
        return False
    hsv = s.get("hsv") or {}
    s_mean = hsv.get("s") if isinstance(hsv, dict) else None
    if s_mean is not None and float(s_mean) < _PLANT_S_MIN_READ:
        return False
    if s.get("G_norm") is not None and s["G_norm"] > _G_NORM_MAX_READ:
        return False
    if ok is True:
        return True
    # Legacy: no quality column — usable if we have some greenness signal
    return s.get("G_norm") is not None or s.get("greenness") is not None or s.get("G_rel") is not None


def compute_color_trends(samples: List[dict]) -> Dict[str, Any]:
    """Lightweight growth/decline signals from normalized greenness + coverage.

    Prefer G_norm when enough good-quality samples exist; fall back to G_rel /
    legacy greenness. Decline alerts only fire on quality-filtered points.
    """
    empty = {
        "sample_count": 0,
        "good_sample_count": 0,
        "greenness_rate_per_day": None,
        "coverage_rate_per_day": None,
        "declining_greenness": False,
        "growing_coverage": False,
        "greenness_delta": None,
        "coverage_delta": None,
        "leaf_area_proxy": None,
        "window_hours": None,
        "trend_metric": None,
        "yellow_fraction_latest": None,
        "G_norm_latest": None,
    }
    if not samples:
        return empty

    latest = samples[-1]
    leaf = latest.get("plant_pixels")
    good = [s for s in samples if _sample_is_good(s)]

    if len(samples) < 2:
        return {
            **empty,
            "sample_count": len(samples),
            "good_sample_count": len(good),
            "leaf_area_proxy": leaf,
            "yellow_fraction_latest": latest.get("yellow_fraction"),
            "G_norm_latest": latest.get("G_norm"),
        }

    t0 = float(samples[0]["sampled_at"])
    t1 = float(samples[-1]["sampled_at"])
    span_s = max(1.0, t1 - t0)
    span_days = span_s / 86400.0
    window_hours = span_s / 3600.0

    def _vals(key: str, source: List[dict]) -> List[Tuple[float, float]]:
        out = []
        for s in source:
            v = s.get(key)
            if v is not None and s.get("sampled_at") is not None:
                out.append((float(s["sampled_at"]), float(v)))
        return out

    def _slope_per_day(pairs: List[Tuple[float, float]]) -> Optional[float]:
        if len(pairs) < 2:
            return None
        (ta, va), (tb, vb) = pairs[0], pairs[-1]
        days = max(1e-6, (tb - ta) / 86400.0)
        return (vb - va) / days

    # Prefer normalized series on good samples (≥3); else G_rel / greenness
    trend_metric = "greenness"
    g_pairs = _vals("G_norm", good)
    if len(g_pairs) >= 3:
        trend_metric = "G_norm"
    else:
        g_pairs = _vals("G_rel", good)
        if len(g_pairs) >= 2:
            trend_metric = "G_rel"
        else:
            g_pairs = _vals("greenness", good if good else samples)
            trend_metric = "greenness"

    c_pairs = _vals("coverage", samples)
    g_rate = _slope_per_day(g_pairs)
    c_rate = _slope_per_day(c_pairs)
    g_delta = (g_pairs[-1][1] - g_pairs[0][1]) if len(g_pairs) >= 2 else None
    c_delta = (c_pairs[-1][1] - c_pairs[0][1]) if len(c_pairs) >= 2 else None

    # Decline only when enough good-quality points support the trend
    declining = False
    enough_quality = len(g_pairs) >= 3 and len(good) >= 3
    if enough_quality:
        # Thresholds scale slightly for G_norm (typically ~0.5–2 vs ~0.3–0.6)
        rate_thresh = -0.008 if trend_metric == "G_norm" else -0.005
        delta_thresh = -0.03 if trend_metric == "G_norm" else -0.02
        if g_rate is not None and g_rate < rate_thresh and span_days >= 0.25:
            declining = True
        if g_delta is not None and g_delta <= delta_thresh and span_days >= 0.5:
            declining = True

    growing = bool(c_rate is not None and c_rate > 0.002 and span_days >= 0.5)

    return {
        "sample_count": len(samples),
        "good_sample_count": len(good),
        "greenness_rate_per_day": round(g_rate, 5) if g_rate is not None else None,
        "coverage_rate_per_day": round(c_rate, 5) if c_rate is not None else None,
        "declining_greenness": declining,
        "growing_coverage": growing,
        "greenness_delta": round(g_delta, 4) if g_delta is not None else None,
        "coverage_delta": round(c_delta, 5) if c_delta is not None else None,
        "leaf_area_proxy": leaf,
        "window_hours": round(window_hours, 2),
        "trend_metric": trend_metric,
        "yellow_fraction_latest": latest.get("yellow_fraction"),
        "G_norm_latest": latest.get("G_norm"),
    }


def save_watering_event(
    monitor_id: str,
    *,
    watered_at: Optional[float] = None,
    note: Optional[str] = None,
) -> dict:
    db = _db()
    now = time.time()
    ts = float(watered_at) if watered_at is not None else now
    note_s = (note or "").strip() or None
    with db._connect() as conn:
        init_plant_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO plant_watering_events (monitor_id, watered_at, note, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (monitor_id, ts, note_s, now),
        )
        conn.commit()
        eid = int(cur.lastrowid)
    return {
        "id": eid,
        "monitor_id": monitor_id,
        "watered_at": ts,
        "note": note_s,
        "created_at": now,
    }


def list_watering_events(
    monitor_id: str,
    *,
    since: Optional[float] = None,
    limit: int = 100,
) -> List[dict]:
    db = _db()
    limit = max(1, int(limit))
    with db._connect() as conn:
        init_plant_tables(conn)
        if since is not None:
            cur = conn.execute(
                """
                SELECT id, monitor_id, watered_at, note, created_at
                FROM plant_watering_events
                WHERE monitor_id = ? AND watered_at >= ?
                ORDER BY watered_at ASC LIMIT ?
                """,
                (monitor_id, float(since), limit),
            )
            rows = cur.fetchall()
        else:
            cur = conn.execute(
                """
                SELECT id, monitor_id, watered_at, note, created_at
                FROM plant_watering_events
                WHERE monitor_id = ?
                ORDER BY watered_at DESC LIMIT ?
                """,
                (monitor_id, limit),
            )
            rows = list(reversed(cur.fetchall()))
    return [{
        "id": r[0],
        "monitor_id": r[1],
        "watered_at": r[2],
        "note": r[3],
        "created_at": r[4],
    } for r in rows]


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
