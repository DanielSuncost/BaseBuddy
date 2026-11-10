"""Aggregation helpers for plant detail plots (daily deltas, etc.)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Match metrics / db night gate — dark frames must not drive G_norm deltas.
_L_BG_LOW = 0.08
_L_G_NORM_MIN = 0.42
_G_NORM_MAX = 5.0
_PLANT_S_MIN = 12.0
_G_NORM_EXCLUDE_FLAGS = (
    "L_bg_low",
    "L_frame_low",
    "L_plant_low",
    "G_norm_clipped",
    "G_norm_dim_light",
    "plant_desaturated",
)


def _sample_ok(s: Dict[str, Any]) -> bool:
    """True when a sample may drive greenness / daily-Δ aggregations."""
    ok = s.get("sample_ok")
    if ok is False:
        return False
    flags = s.get("quality_flags") or []
    if any(f in flags for f in _G_NORM_EXCLUDE_FLAGS):
        return False
    L_bg = s.get("L_bg")
    if L_bg is not None and float(L_bg) < _L_G_NORM_MIN:
        return False
    L_frame = s.get("L_frame")
    if L_frame is not None and float(L_frame) < _L_G_NORM_MIN:
        return False
    # Legacy rows: desaturated plant region (grow light / IR wash)
    hsv = s.get("hsv") or {}
    s_mean = hsv.get("s") if isinstance(hsv, dict) else None
    if s_mean is not None and float(s_mean) < _PLANT_S_MIN:
        return False
    g = s.get("G_norm")
    if g is not None and float(g) > _G_NORM_MAX:
        return False
    if ok is True:
        return True
    # Legacy null sample_ok: usable only with a finite, non-spike G_norm (or fallback).
    if g is not None and float(g) <= _G_NORM_MAX:
        return True
    return s.get("greenness") is not None or s.get("G_rel") is not None


def _metric_val(s: Dict[str, Any], key: str) -> Optional[float]:
    if key == "G_norm":
        v = s.get("G_norm")
        if v is None:
            return None
        fv = float(v)
        if fv > _G_NORM_MAX:
            return None
        return fv
    if key == "G_rel":
        v = s.get("G_rel")
        if v is not None:
            return float(v)
        v = s.get("greenness")
        return float(v) if v is not None else None
    v = s.get(key)
    return float(v) if v is not None else None


def daily_metric_deltas(
    samples: List[Dict[str, Any]],
    key: str = "G_norm",
    *,
    good_only: bool = True,
) -> List[Dict[str, Any]]:
    """One value per UTC calendar day (last sample of the day), then day-over-day Δ.

    Returns list of {day, ts, value, delta} sorted ascending. First day has delta=None.
    When good_only, poor-quality / low-light samples are skipped for the daily value.
    """
    by_day: Dict[str, Dict[str, Any]] = {}
    for s in samples or []:
        ts = s.get("sampled_at")
        if ts is None:
            continue
        if good_only and not _sample_ok(s):
            continue
        val = _metric_val(s, key)
        if val is None:
            continue
        day = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        prev = by_day.get(day)
        if prev is None or float(ts) >= float(prev["ts"]):
            by_day[day] = {"day": day, "ts": float(ts), "value": val, "delta": None}

    ordered = [by_day[k] for k in sorted(by_day.keys())]
    for i in range(1, len(ordered)):
        ordered[i]["delta"] = round(ordered[i]["value"] - ordered[i - 1]["value"], 5)
    return ordered
