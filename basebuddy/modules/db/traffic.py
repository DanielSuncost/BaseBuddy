"""Traffic analysis track storage and aggregate queries.

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


class TrafficMixin:
    # ---------------- Traffic analysis helpers -----------------
    def save_traffic_track(self, camera_id: int, track_id: int, points: list, px_per_m: int,
                           region_label: str = None, class_name: str = None) -> bool:
        """Persist a finalized track with derived speed/direction metrics.

        points: list of (x, y, ts) tuples (pixels, epoch seconds)
        """
        try:
            if not points or len(points) < 2:
                return False

            x1, y1, t1 = points[0]
            x2, y2, t2 = points[-1]
            duration = max(0.001, float(t2) - float(t1))

            dx = float(x2) - float(x1)
            dy = float(y2) - float(y1)
            dist_px = (dx**2 + dy**2) ** 0.5
            dist_m = float(dist_px) / float(max(1, px_per_m))
            speed_mps = dist_m / duration
            speed_kph = speed_mps * 3.6

            import math, json as _json
            direction_rad = math.atan2(-dy, dx)  # screen y grows down
            direction_deg = (math.degrees(direction_rad) + 360.0) % 360.0
            path_json = _json.dumps([{'x': float(x), 'y': float(y), 't': float(t)} for x, y, t in points])

            # Retry logic for database locked errors
            max_retries = 5
            retry_delay = 0.1
            
            for attempt in range(max_retries):
                try:
                    conn = self._connect()  # Use proper connection with WAL mode and timeout
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO traffic_tracks (
                            camera_id, track_id, start_ts, end_ts, duration_s,
                            start_x, start_y, end_x, end_y,
                            distance_px, distance_m, speed_mps, speed_kph, direction_deg, path_json,
                            region_label, class_name
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        int(camera_id), int(track_id), float(t1), float(t2), float(duration),
                        float(x1), float(y1), float(x2), float(y2),
                        float(dist_px), float(dist_m), float(speed_mps), float(speed_kph), float(direction_deg), path_json,
                        region_label, class_name,
                    ))
                    conn.commit()
                    conn.close()
                    return True
                    
                except sqlite3.OperationalError as e:
                    if 'locked' in str(e).lower() and attempt < max_retries - 1:
                        import time
                        time.sleep(retry_delay * (2 ** attempt))
                        try:
                            conn.close()
                        except Exception:
                            pass
                        continue
                    else:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        raise
                        
        except Exception as e:
            logger.error(f"Error saving traffic track: {e}")
            return False

    def get_traffic_hourly_stats(self, date: str, camera_id: Optional[int] = None,
                                 region_label: Optional[str] = None,
                                 class_name: Optional[str] = None) -> list:
        """Return hourly counts and avg speeds for the given local date (YYYY-MM-DD)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            params = [date]
            extra = ''
            if camera_id is not None:
                extra += ' AND camera_id = ?'
                params.append(camera_id)
            if region_label:
                extra += ' AND region_label = ?'
                params.append(region_label)
            if class_name:
                extra += ' AND class_name = ?'
                params.append(class_name)
            cursor.execute(f'''
                SELECT CAST(strftime('%H', datetime(start_ts, 'unixepoch', 'localtime')) AS INTEGER) AS hour,
                       COUNT(*), AVG(speed_kph)
                FROM traffic_tracks
                WHERE date(datetime(start_ts, 'unixepoch', 'localtime')) = ? {extra}
                GROUP BY hour
                ORDER BY hour
            ''', params)
            rows = cursor.fetchall()
            return [{'hour': r[0], 'count': r[1], 'avg_speed_kph': round(r[2], 2) if r[2] is not None else 0} for r in rows]

    def get_traffic_direction_stats(self, date: str, camera_id: Optional[int] = None,
                                    bin_size_deg: int = 45, region_label: Optional[str] = None,
                                    class_name: Optional[str] = None) -> list:
        """Return direction bin counts (0-360, bins of bin_size_deg)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            params = [date]
            extra = ''
            if camera_id is not None:
                extra += ' AND camera_id = ?'
                params.append(camera_id)
            if region_label:
                extra += ' AND region_label = ?'
                params.append(region_label)
            if class_name:
                extra += ' AND class_name = ?'
                params.append(class_name)

            cursor.execute(f'''
                SELECT direction_deg FROM traffic_tracks
                WHERE date(datetime(start_ts, 'unixepoch', 'localtime')) = ? {extra}
            ''', params)
            dirs = [float(r[0]) for r in cursor.fetchall()]

            if bin_size_deg <= 0:
                bin_size_deg = 45
            bins = int(360 / bin_size_deg)
            counts = [0] * bins
            for d in dirs:
                idx = int(d // bin_size_deg) % bins
                counts[idx] += 1
            return [{'start_deg': i*bin_size_deg, 'end_deg': (i+1)*bin_size_deg, 'count': counts[i]} for i in range(bins)]

    def get_recent_traffic_tracks(self, camera_id: int, limit: int = 50,
                                  region_label: Optional[str] = None,
                                  class_name: Optional[str] = None) -> list:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            params = [camera_id]
            extra = ''
            if region_label:
                extra += ' AND region_label = ?'
                params.append(region_label)
            if class_name:
                extra += ' AND class_name = ?'
                params.append(class_name)
            params.append(limit)
            cursor.execute(f'''
                SELECT id, start_ts, end_ts, speed_kph, direction_deg, distance_m, region_label, class_name
                FROM traffic_tracks WHERE camera_id = ? {extra}
                ORDER BY id DESC
                LIMIT ?
            ''', params)
            rows = cursor.fetchall()
            return [
                {
                    'id': r[0],
                    'start_ts': r[1],
                    'end_ts': r[2],
                    'speed_kph': round(r[3], 2) if r[3] is not None else 0,
                    'direction_deg': round(r[4], 1) if r[4] is not None else 0,
                    'distance_m': round(r[5], 2) if r[5] is not None else 0,
                    'region_label': r[6] or '',
                    'class_name': r[7] or '',
                }
                for r in rows
            ]

    def get_traffic_paths(self, camera_id: int, start_ts: float, end_ts: float,
                          region_label: Optional[str] = None,
                          class_name: Optional[str] = None,
                          limit: int = 2000) -> list:
        """Full track paths for a camera within [start_ts, end_ts].

        Returns dicts with the decoded point list plus direction/class metadata,
        newest first, capped at `limit` tracks.
        """
        import json as _json
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            params = [camera_id, float(start_ts), float(end_ts)]
            extra = ''
            if region_label:
                extra += ' AND region_label = ?'
                params.append(region_label)
            if class_name:
                extra += ' AND class_name = ?'
                params.append(class_name)
            params.append(int(limit))
            cursor.execute(f'''
                SELECT path_json, direction_deg, class_name, speed_kph, start_ts, end_ts
                FROM traffic_tracks
                WHERE camera_id = ? AND end_ts >= ? AND start_ts <= ? {extra}
                ORDER BY id DESC
                LIMIT ?
            ''', params)
            out = []
            for r in cursor.fetchall():
                try:
                    points = _json.loads(r[0]) if r[0] else []
                except Exception:
                    points = []
                if len(points) < 2:
                    continue
                out.append({
                    'points': points,
                    'direction_deg': float(r[1]) if r[1] is not None else 0.0,
                    'class_name': r[2] or '',
                    'speed_kph': float(r[3]) if r[3] is not None else 0.0,
                    'start_ts': r[4],
                    'end_ts': r[5],
                })
            return out

    def get_traffic_sources(self) -> list:
        """Distinct (camera, region, class) combos with track counts, most recent first."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT camera_id, COALESCE(region_label, ''), COALESCE(class_name, ''),
                       COUNT(*), MAX(start_ts)
                FROM traffic_tracks
                GROUP BY camera_id, region_label, class_name
                ORDER BY MAX(start_ts) DESC
            ''')
            return [
                {
                    'camera_id': r[0],
                    'region_label': r[1],
                    'class_name': r[2],
                    'track_count': r[3],
                    'last_seen_ts': r[4],
                }
                for r in cursor.fetchall()
            ]
