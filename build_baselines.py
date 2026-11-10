#!/usr/bin/env python3
"""Build normal-activity baselines for BaseBuddy exterior cameras.

Run on the OMEN (where analytics.db lives), READ-ONLY:

  python3 build_baselines.py \\
    --db /home/dapppo/Projects/BaseBuddyClean/analytics.db \\
    --out /tmp/baselines.json

Then copy baselines.json to the box at
  /home/box/agent-data/basebuddy/baselines.json

Never prints or saves camera stream URLs.
Does not modify BaseBuddy settings, code, or the database.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Exterior detection cameras to profile (exclude Rosemary Landing = 5)
CAMERAS = {
    0: "Front of House",
    1: "Back Entrance",
    2: "Camera 3",
    3: "Camera 4",
    9: "Front Yard",
}

CLASSES = ("person", "car", "bicycle", "cat")  # no dog in history
ANIMAL_CLASSES = ("cat",)  # reserved if dog appears later

# Known total-detection outages (America/New_York local wall times)
KNOWN_OUTAGES = [
    # (start_local_iso, end_local_iso, reason)
    ("2026-09-30T16:00:00", "2026-10-14T20:00:00", "NVIDIA NVML mismatch / unreachable"),
    ("2026-10-15T17:22:00", "2026-10-17T23:01:00", "detection stall after clip-buffer flood"),
    ("2026-10-19T09:03:00", "2026-10-19T09:26:00", "all-camera detection stall"),
    ("2026-08-31T19:00:00", "2026-09-01T16:00:00", "multi-hour total gap"),
    ("2026-07-14T00:00:00", "2026-07-18T17:00:00", "multi-day total gap"),
    ("2026-07-18T20:00:00", "2026-07-20T22:00:00", "multi-day total gap"),
]

DEDUP_WINDOW_SEC = 300  # 5 minutes
MIN_OUTAGE_GAP_HOURS = 2  # auto-detected contiguous zero-hour blocks


def parse_local(iso: str) -> float:
    return datetime.fromisoformat(iso).replace(tzinfo=ET).timestamp()


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))


def build_known_outage_hours() -> set[int]:
    hours: set[int] = set()
    for start_iso, end_iso, _ in KNOWN_OUTAGES:
        s = int(parse_local(start_iso) // 3600) * 3600
        e = int(parse_local(end_iso) // 3600) * 3600
        h = s
        while h <= e:
            hours.add(h)
            h += 3600
    return hours


def discover_outage_hours(con: sqlite3.Connection, cam_ids: list[int]) -> set[int]:
    """Hours with zero sessions across ALL target cameras; keep blocks >= MIN_OUTAGE_GAP_HOURS."""
    placeholders = ",".join("?" * len(cam_ids))
    rows = con.execute(
        f"""
        SELECT CAST(started_at/3600 AS INTEGER)*3600 AS hour_ts
        FROM event_sessions
        WHERE camera_id IN ({placeholders})
        GROUP BY 1
        ORDER BY 1
        """,
        cam_ids,
    ).fetchall()
    if not rows:
        return set()
    present = {r[0] for r in rows}
    first_h, last_h = rows[0][0], rows[-1][0]
    gaps: list[int] = []
    h = first_h
    while h <= last_h:
        if h not in present:
            gaps.append(h)
        h += 3600
    out: set[int] = set()
    if not gaps:
        return out
    # collapse consecutive
    block_start = block_end = gaps[0]
    blocks = []
    for g in gaps[1:]:
        if g == block_end + 3600:
            block_end = g
        else:
            blocks.append((block_start, block_end))
            block_start = block_end = g
    blocks.append((block_start, block_end))
    for s, e in blocks:
        dur_h = (e - s) // 3600 + 1
        if dur_h >= MIN_OUTAGE_GAP_HOURS:
            h = s
            while h <= e:
                out.add(h)
                h += 3600
    return out


def stream_aggregate(con: sqlite3.Connection, cam_ids: list[int], outage_hours: set[int]):
    """
    One pass over event_sessions for target cams.
    Returns dict keyed by (camera_id, class_name, day_str, hour) -> metrics.
    Metrics: sessions, active_minutes (set size), deduped_5min.
    Skips rows whose hour_ts is in outage_hours.
    """
    placeholders = ",".join("?" * len(cam_ids))
    # Per (cam, class, day, hour):
    sessions = defaultdict(int)
    minutes: dict[tuple, set] = defaultdict(set)
    # For dedup: last kept started_at per (cam, class, day, hour) chain — actually
    # dedupe across the day continuously within each cam+class, then bucket by hour
    # of the *first* event in each burst. Simpler: within each hour bucket, greedily
    # collapse events within DEDUP_WINDOW_SEC.
    hour_event_times: dict[tuple, list] = defaultdict(list)

    cur = con.execute(
        f"""
        SELECT camera_id, class_name, started_at
        FROM event_sessions
        WHERE camera_id IN ({placeholders})
          AND class_name IN ('person','car','bicycle','cat','dog')
        ORDER BY camera_id, class_name, started_at
        """,
        cam_ids,
    )

    # Greedy global dedup state per (cam, class)
    last_kept: dict[tuple, float] = {}
    deduped = defaultdict(int)

    batch = 0
    for camera_id, class_name, started_at in cur:
        hour_ts = int(started_at // 3600) * 3600
        if hour_ts in outage_hours:
            continue
        dt = datetime.fromtimestamp(started_at, ET)
        day_str = dt.strftime("%Y-%m-%d")
        hour = dt.hour
        key = (camera_id, class_name, day_str, hour)

        sessions[key] += 1
        minutes[key].add(int(started_at // 60))

        dk = (camera_id, class_name)
        prev = last_kept.get(dk)
        if prev is None or (started_at - prev) >= DEDUP_WINDOW_SEC:
            last_kept[dk] = started_at
            deduped[key] += 1
        # else: absorbed into previous burst (attribute stays on earlier hour)

        batch += 1
        if batch % 1_000_000 == 0:
            print(f"  …processed {batch:,} rows", flush=True)

    print(f"  processed {batch:,} rows total", flush=True)

    # Merge into records
    records = {}
    all_keys = set(sessions) | set(minutes) | set(deduped)
    for key in all_keys:
        records[key] = {
            "sessions": sessions.get(key, 0),
            "active_minutes": len(minutes.get(key, ())),
            "deduped_5min": deduped.get(key, 0),
        }
    return records


def summarize(records: dict) -> dict:
    """
    Build per-camera, per-class, weekday/weekend, hour-of-day medians and p90.
    Also fill missing non-outage hours? We only have hours that had >=1 session
    of that class. For a true quiet baseline we need zeros for hours with no
    sessions of that class on days when the system was up.

    Approach: for each camera, collect the set of (day, hour) that appear for
    ANY class on that camera (system was producing detections). Then for each
    class, treat missing (day,hour) as zero for that class.
    """
    # system-up day-hours per camera
    up_slots: dict[int, set] = defaultdict(set)
    for (cam, cls, day, hour) in records:
        up_slots[cam].add((day, hour))

    # Also need all classes observed
    by_cam_class_slot = records  # key -> metrics

    profiles = {}
    meta_days = set()

    for cam_id, cam_name in CAMERAS.items():
        cam_profile = {"camera_id": cam_id, "name": cam_name, "classes": {}}
        slots = up_slots.get(cam_id, set())
        for day, _hour in slots:
            meta_days.add(day)

        for cls in CLASSES:
            # Collect values per (day_type, hour)
            buckets = {
                "weekday": {h: {"sessions": [], "active_minutes": [], "deduped_5min": []} for h in range(24)},
                "weekend": {h: {"sessions": [], "active_minutes": [], "deduped_5min": []} for h in range(24)},
            }
            for day, hour in slots:
                dt = datetime.strptime(day, "%Y-%m-%d")
                day_type = "weekend" if dt.weekday() >= 5 else "weekday"
                key = (cam_id, cls, day, hour)
                m = by_cam_class_slot.get(key, {"sessions": 0, "active_minutes": 0, "deduped_5min": 0})
                for metric in ("sessions", "active_minutes", "deduped_5min"):
                    buckets[day_type][hour][metric].append(m[metric])

            class_out = {"weekday": {}, "weekend": {}, "sample_hours": {}}
            for day_type in ("weekday", "weekend"):
                for hour in range(24):
                    entry = {}
                    n = len(buckets[day_type][hour]["active_minutes"])
                    entry["n"] = n
                    for metric in ("sessions", "active_minutes", "deduped_5min"):
                        vals = sorted(buckets[day_type][hour][metric])
                        if not vals:
                            entry[metric] = {"median": None, "p90": None}
                        else:
                            entry[metric] = {
                                "median": round(statistics.median(vals), 2),
                                "p90": round(percentile(vals, 90), 2),
                            }
                    class_out[day_type][str(hour)] = entry
                class_out["sample_hours"][day_type] = sum(
                    len(buckets[day_type][h]["active_minutes"]) for h in range(24)
                )
            cam_profile["classes"][cls] = class_out
        profiles[str(cam_id)] = cam_profile

    return profiles, sorted(meta_days)


def busy_quiet_summary(profiles: dict) -> dict:
    """Plain-English helpers: busy/quiet hours by person active_minutes median."""
    summaries = {}
    for cam_id, cam in profiles.items():
        person = cam["classes"].get("person", {})
        cam_sum = {"weekday": {}, "weekend": {}}
        for day_type in ("weekday", "weekend"):
            hours = person.get(day_type, {})
            scored = []
            for h, entry in hours.items():
                am = entry.get("active_minutes", {})
                med = am.get("median")
                if med is None or entry.get("n", 0) < 2:
                    continue
                scored.append((int(h), med, am.get("p90"), entry["n"]))
            if not scored:
                cam_sum[day_type] = {"note": "insufficient samples"}
                continue
            scored.sort(key=lambda x: x[1], reverse=True)
            busy = sorted([x[0] for x in scored[:5]])
            quiet = sorted([x[0] for x in scored if x[1] <= 1.0] or [x[0] for x in scored[-5:]])
            # typical daytime people active minutes
            day_hours = [x for x in scored if 8 <= x[0] <= 20]
            night_hours = [x for x in scored if x[0] <= 5 or x[0] >= 22]
            cam_sum[day_type] = {
                "busy_hours_et": busy,
                "quiet_hours_et": quiet,
                "typical_daytime_person_active_minutes_median": (
                    round(statistics.median([x[1] for x in day_hours]), 2) if day_hours else None
                ),
                "typical_night_person_active_minutes_median": (
                    round(statistics.median([x[1] for x in night_hours]), 2) if night_hours else None
                ),
                "hour_medians_person_active_minutes": {str(h): med for h, med, _p, _n in scored},
            }
        summaries[cam_id] = {"name": cam["name"], "person": cam_sum}
    return summaries


def stationary_noise_notes(con: sqlite3.Connection, outage_hours: set[int]) -> list[dict]:
    """Confirm parked-object re-detection by looking at recent high-rate hours."""
    notes = []
    # Look at last 7 non-outage days of data for car sessions/hour vs active_minutes
    # Quick: top car session rates
    for cam_id, label, cls, expected in [
        (0, "Front of House", "car", "parked cars re-detected continuously"),
        (9, "Front Yard", "car", "parked cars re-detected continuously"),
        (2, "Camera 3", "bicycle", "parked bike re-detected repeatedly"),
        (1, "Back Entrance", "bicycle", "possible parked/leaning bike noise"),
    ]:
        row = con.execute(
            """
            SELECT
              AVG(cnt) avg_sessions,
              MAX(cnt) max_sessions
            FROM (
              SELECT CAST(started_at/3600 AS INTEGER)*3600 AS h, COUNT(*) cnt
              FROM event_sessions
              WHERE camera_id=? AND class_name=?
              GROUP BY 1
            )
            """,
            (cam_id, cls),
        ).fetchone()
        notes.append({
            "camera_id": cam_id,
            "camera": label,
            "class": cls,
            "avg_sessions_per_hour_all_history": round(row[0] or 0, 1),
            "max_sessions_per_hour_all_history": row[1] or 0,
            "note": expected,
        })
    return notes


def suggested_thresholds(profiles: dict) -> dict:
    """
    Unusual = person active_minutes > p90 for that camera/day_type/hour,
    or any person activity in hours whose median active_minutes is 0.
    """
    out = {
        "recommended_metric": "active_minutes",
        "recommended_metric_reason": (
            "Session counts are inflated by track resets and stationary re-detection. "
            "5-minute dedup helps but still counts continuous parked objects once per window. "
            "Distinct active minutes per hour tracks real presence duration for people/animals/"
            "bikes without equating thousands of flash sessions to thousands of visitors."
        ),
        "rules": [
            "Flag unusual if person.active_minutes > that hour's weekday/weekend p90 (and n>=3 samples).",
            "Flag unusual if person.active_minutes >= 1 during a normally-dead hour (median==0, n>=3).",
            "Do NOT use raw car session counts on Front of House or Front Yard for unusual alerts.",
            "Treat car active_minutes on Front of House / Front Yard as presence-of-parked-vehicle noise; prefer person/bicycle/cat.",
            "Camera 3 bicycle spikes often reflect a parked bike; prefer person/cat there for alerts.",
        ],
        "per_camera": {},
    }
    for cam_id, cam in profiles.items():
        person = cam["classes"]["person"]
        dead = {"weekday": [], "weekend": []}
        p90_by_hour = {"weekday": {}, "weekend": {}}
        for day_type in ("weekday", "weekend"):
            for h, entry in person[day_type].items():
                am = entry["active_minutes"]
                if entry["n"] >= 3 and am["median"] == 0:
                    dead[day_type].append(int(h))
                if entry["n"] >= 3 and am["p90"] is not None:
                    p90_by_hour[day_type][h] = am["p90"]
        out["per_camera"][cam_id] = {
            "name": cam["name"],
            "normally_dead_hours_et": dead,
            "person_active_minutes_p90_by_hour": p90_by_hour,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to analytics.db")
    ap.add_argument("--out", required=True, help="Output JSON path")
    args = ap.parse_args()

    uri = f"file:{args.db}?mode=ro"
    con = sqlite3.connect(uri, uri=True)

    cam_ids = list(CAMERAS.keys())
    print("Building outage hour set…", flush=True)
    known = build_known_outage_hours()
    discovered = discover_outage_hours(con, cam_ids)
    outage_hours = known | discovered
    print(f"  known outage hours: {len(known)}, discovered: {len(discovered)}, union: {len(outage_hours)}", flush=True)

    print("Streaming aggregates (may take a few minutes on ~6M rows)…", flush=True)
    records = stream_aggregate(con, cam_ids, outage_hours)

    print("Summarizing profiles…", flush=True)
    profiles, days = summarize(records)
    summaries = busy_quiet_summary(profiles)
    noise = stationary_noise_notes(con, outage_hours)
    thresholds = suggested_thresholds(profiles)

    # Usable day count: days that appear in profiles after outage exclusion
    weekday_n = sum(1 for d in days if datetime.strptime(d, "%Y-%m-%d").weekday() < 5)
    weekend_n = sum(1 for d in days if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5)

    first_ts, last_ts = con.execute(
        f"""
        SELECT MIN(started_at), MAX(started_at) FROM event_sessions
        WHERE camera_id IN ({",".join("?" * len(cam_ids))})
        """,
        cam_ids,
    ).fetchone()

    outage_ranges = []
    for start_iso, end_iso, reason in KNOWN_OUTAGES:
        outage_ranges.append({"start_et": start_iso, "end_et": end_iso, "reason": reason})
    # also list discovered-only long gaps for transparency
    disc_only = sorted(discovered - known)
    if disc_only:
        # collapse
        s = e = disc_only[0]
        extra = []
        for h in disc_only[1:]:
            if h == e + 3600:
                e = h
            else:
                extra.append((s, e))
                s = e = h
        extra.append((s, e))
        for s, e in extra:
            outage_ranges.append({
                "start_et": datetime.fromtimestamp(s, ET).isoformat(),
                "end_et": datetime.fromtimestamp(e + 3600, ET).isoformat(),
                "reason": "auto-detected total gap (>=2h, all target cams quiet)",
            })

    payload = {
        "generated_at_et": datetime.now(ET).isoformat(),
        "timezone": "America/New_York",
        "cameras": CAMERAS,
        "excluded_cameras": {"5": "Rosemary Landing"},
        "classes": list(CLASSES),
        "metrics": {
            "sessions": "raw event_sessions row count (inflated by track resets / stationary re-detect)",
            "active_minutes": "distinct minutes with >=1 session of that class (recommended)",
            "deduped_5min": "greedy bursts collapsed if within 300s of previous kept event, same cam+class",
        },
        "recommended_metric": "active_minutes",
        "data_range": {
            "first_et": datetime.fromtimestamp(first_ts, ET).isoformat() if first_ts else None,
            "last_et": datetime.fromtimestamp(last_ts, ET).isoformat() if last_ts else None,
            "usable_days": len(days),
            "usable_weekdays": weekday_n,
            "usable_weekend_days": weekend_n,
            "usable_day_list": days,
            "thin_data_warning": (
                None
                if len(days) >= 21
                else (
                    f"Only {len(days)} usable days after excluding outages "
                    f"({weekday_n} weekday, {weekend_n} weekend). "
                    "Treat profiles as provisional, especially weekend hour bins."
                )
            ),
        },
        "outages_excluded": outage_ranges,
        "outage_hours_excluded_count": len(outage_hours),
        "stationary_noise": noise,
        "profiles": profiles,
        "plain_english_summaries": summaries,
        "suggested_unusual_thresholds": thresholds,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    print(f"Wrote {args.out}", flush=True)
    print(
        f"Usable days: {len(days)} ({weekday_n} weekday / {weekend_n} weekend)",
        flush=True,
    )
    if payload["data_range"]["thin_data_warning"]:
        print("WARNING:", payload["data_range"]["thin_data_warning"], flush=True)
    con.close()


if __name__ == "__main__":
    main()
