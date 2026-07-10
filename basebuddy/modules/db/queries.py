"""Read-side detection/gallery/stats queries.

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


class DetectionQueriesMixin:
    def get_today_stats(self) -> Dict[str, Any]:
        """Get today's detection statistics"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Get total detections today
            cursor.execute('''
                SELECT COUNT(*) FROM events
                WHERE DATE(timestamp) = ?
            ''', (today,))

            total_detections = cursor.fetchone()[0]

            # Get detections by class
            cursor.execute('''
                SELECT class_name, COUNT(*), AVG(confidence)
                FROM events
                WHERE DATE(timestamp) = ?
                GROUP BY class_name
                ORDER BY COUNT(*) DESC
            ''', (today,))

            class_stats = [
                {
                    "class": row[0],
                    "count": row[1],
                    "avg_confidence": round(row[2], 3) if row[2] else 0
                }
                for row in cursor.fetchall()
            ]

            return {
                "total_detections": total_detections,
                "class_stats": class_stats
            }

    def get_hourly_stats(self, date: str = None, camera_id: int = None) -> List[Dict[str, Any]]:
        """Get hourly statistics for a date"""
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            query = '''
                SELECT camera_id, hour, class_name, count, avg_confidence
                FROM hourly_stats
                WHERE date = ?
            '''
            params = [date]

            if camera_id is not None:
                query += ' AND camera_id = ?'
                params.append(camera_id)

            query += ' ORDER BY hour, class_name'

            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    "camera_id": row[0],
                    "hour": row[1],
                    "class_name": row[2],
                    "count": row[3],
                    "avg_confidence": round(row[4], 3) if row[4] else 0
                })

            return results

    def get_detection_events(self, camera_id: int = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get recent detection events"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            query = '''
                SELECT id, camera_id, timestamp, class_name, confidence,
                       bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path
                FROM events
            '''
            params = []

            if camera_id is not None:
                query += ' WHERE camera_id = ?'
                params.append(camera_id)

            query += ' ORDER BY timestamp DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])

            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "camera_id": row[1],
                    "timestamp": row[2],
                    "class_name": row[3],
                    "confidence": round(row[4], 3) if row[4] else 0,
                    "bbox": [row[5], row[6], row[7], row[8]],
                    "track_id": row[9],
                    "thumbnail_path": row[10],
                    "full_image_path": row[11]
                })

            return results

    def get_detection_events_for_date(self, date: str, camera_id: int = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get detection events for a specific date"""
        with self._connect() as conn:
            cursor = conn.cursor()

            query = '''
                SELECT id, camera_id, timestamp, class_name, confidence,
                       bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path
                FROM events
                WHERE DATE(timestamp) = ?
                  AND training_label IS NULL
            '''
            params = [date]

            if camera_id is not None:
                query += ' AND camera_id = ?'
                params.append(camera_id)

            query += ' ORDER BY timestamp DESC'
            if limit is not None and limit > 0:
                query += ' LIMIT ?'
                params.append(limit)
                if offset and offset > 0:
                    query += ' OFFSET ?'
                    params.append(offset)

            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "camera_id": row[1],
                    "timestamp": row[2],
                    "class_name": row[3],
                    "confidence": round(row[4], 3) if row[4] else 0,
                    "bbox": [row[5], row[6], row[7], row[8]],
                    "track_id": row[9],
                    "thumbnail_path": row[10],
                    "full_image_path": row[11]
                })

            return results

    def get_recent_detections(self, hours: int = 1, camera_id: int = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get detection events from the last N hours"""
        with self._connect() as conn:
            cursor = conn.cursor()

            query = f'''
                SELECT id, camera_id, timestamp, class_name, confidence,
                       bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path
                FROM events
                WHERE timestamp >= datetime('now', '-{hours} hours')
                  AND {self._GALLERY_LABEL_FILTER}
            '''
            params = []

            if camera_id is not None:
                query += ' AND camera_id = ?'
                params.append(camera_id)

            query += ' ORDER BY timestamp DESC'
            if limit is not None and limit > 0:
                query += ' LIMIT ?'
                params.append(limit)
                if offset and offset > 0:
                    query += ' OFFSET ?'
                    params.append(offset)

            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "camera_id": row[1],
                    "timestamp": row[2],
                    "class_name": row[3],
                    "confidence": round(row[4], 3) if row[4] else 0,
                    "bbox": [row[5], row[6], row[7], row[8]],
                    "track_id": row[9],
                    "thumbnail_path": row[10],
                    "full_image_path": row[11]
                })

            return results

    def get_recent_detections_by_class(self, class_name: str, hours: int = 1, camera_id: int = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get recent detection events for a specific class"""
        with self._connect() as conn:
            cursor = conn.cursor()

            query = f'''
                SELECT id, camera_id, timestamp, class_name, confidence,
                       bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path
                FROM events
                WHERE timestamp >= datetime('now', '-{hours} hours') AND class_name = ?
                  AND {self._GALLERY_LABEL_FILTER}
            '''
            params = [class_name]

            if camera_id is not None:
                query += ' AND camera_id = ?'
                params.append(camera_id)

            query += ' ORDER BY timestamp DESC'
            if limit is not None and limit > 0:
                query += ' LIMIT ?'
                params.append(limit)
                if offset and offset > 0:
                    query += ' OFFSET ?'
                    params.append(offset)

            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "camera_id": row[1],
                    "timestamp": row[2],
                    "class_name": row[3],
                    "confidence": round(row[4], 3) if row[4] else 0,
                    "bbox": [row[5], row[6], row[7], row[8]],
                    "track_id": row[9],
                    "thumbnail_path": row[10],
                    "full_image_path": row[11]
                })

            return results

    @staticmethod
    def _gallery_grouping_cte(where_str: str) -> str:
        """SQL CTE that assigns group_key / group_type (shared by gallery list + group modal)."""
        return f'''
            WITH position_groups AS (
                SELECT 
                    id, camera_id, timestamp, class_name, confidence,
                    bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path,
                    camera_id || '_' || class_name || '_' || 
                    CAST(ROUND(bbox_x1/50)*50 AS INT) || '_' || 
                    CAST(ROUND(bbox_y1/50)*50 AS INT) || '_' || 
                    CAST(ROUND((bbox_x2-bbox_x1)/50)*50 AS INT) || '_' || 
                    CAST(ROUND((bbox_y2-bbox_y1)/50)*50 AS INT) as pos_group_key,
                    COUNT(*) OVER (
                        PARTITION BY camera_id, track_id, class_name,
                            CAST(ROUND(bbox_x1/50)*50 AS INT),
                            CAST(ROUND(bbox_y1/50)*50 AS INT),
                            CAST(ROUND((bbox_x2-bbox_x1)/50)*50 AS INT),
                            CAST(ROUND((bbox_y2-bbox_y1)/50)*50 AS INT)
                    ) as same_pos_count,
                    COUNT(*) OVER (
                        PARTITION BY camera_id, track_id, class_name
                    ) as track_total_count
                FROM events
                WHERE {where_str}
            ),
            grouped_events AS (
                SELECT 
                    id, camera_id, timestamp, class_name, confidence,
                    bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path,
                    CASE 
                        WHEN track_id IS NOT NULL AND same_pos_count = track_total_count THEN
                            pos_group_key
                        WHEN track_id IS NOT NULL THEN
                            camera_id || '_track_' || track_id || '_' || class_name
                        ELSE
                            pos_group_key
                    END as group_key,
                    CASE 
                        WHEN track_id IS NOT NULL AND same_pos_count = track_total_count THEN 'similar'
                        WHEN track_id IS NOT NULL THEN 'track'
                        ELSE 'similar'
                    END as group_type,
                    ROW_NUMBER() OVER (
                        PARTITION BY 
                            CASE 
                                WHEN track_id IS NOT NULL AND same_pos_count = track_total_count THEN
                                    pos_group_key
                                WHEN track_id IS NOT NULL THEN
                                    camera_id || '_track_' || track_id || '_' || class_name
                                ELSE
                                    pos_group_key
                            END
                        ORDER BY timestamp DESC
                    ) as rn,
                    COUNT(*) OVER (
                        PARTITION BY 
                            CASE 
                                WHEN track_id IS NOT NULL AND same_pos_count = track_total_count THEN
                                    pos_group_key
                                WHEN track_id IS NOT NULL THEN
                                    camera_id || '_track_' || track_id || '_' || class_name
                                ELSE
                                    pos_group_key
                            END
                    ) as group_count
                FROM position_groups
            )
            '''

    def _gallery_group_where(
        self,
        *,
        date_filter: str = None,
        hours: int = None,
        camera_ids: list = None,
        camera_id: int = None,
        class_filter: str = None,
    ) -> tuple[str, list]:
        params: list = []
        if date_filter:
            where_clauses = ["DATE(timestamp) = ?", self._GALLERY_LABEL_FILTER]
            params = [date_filter]
        elif hours is not None and int(hours) > 0:
            where_clauses = [
                f"timestamp >= datetime('now', '-{int(hours)} hours')",
                self._GALLERY_LABEL_FILTER,
            ]
        else:
            where_clauses = [
                "timestamp >= datetime('now', '-1 hours')",
                self._GALLERY_LABEL_FILTER,
            ]

        if camera_ids:
            placeholders = ",".join("?" * len(camera_ids))
            where_clauses.append(f"camera_id IN ({placeholders})")
            params.extend(camera_ids)
        elif camera_id is not None:
            where_clauses.append("camera_id = ?")
            params.append(camera_id)

        if class_filter:
            where_clauses.append("class_name = ?")
            params.append(class_filter)

        return " AND ".join(where_clauses), params

    def _detection_dict_from_row(self, row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "camera_id": row[1],
            "timestamp": row[2],
            "class_name": row[3],
            "confidence": round(row[4], 3) if row[4] else 0,
            "bbox": [row[5], row[6], row[7], row[8]],
            "track_id": row[9],
            "thumbnail_path": row[10],
            "full_image_path": row[11],
        }

    def _filter_resolvable_detections(self, detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Drop rows whose image paths no longer exist on disk (stale track history)."""
        try:
            from basebuddy.core.services.media_paths import url_to_filesystem
        except ImportError:
            return detections

        kept: List[Dict[str, Any]] = []
        for det in detections:
            thumb = det.get("thumbnail_path")
            full = det.get("full_image_path")
            if url_to_filesystem(thumb) or url_to_filesystem(full):
                kept.append(det)
        return kept

    def get_unique_detections_for_date(self, date: str, camera_id: int = None, camera_ids: list = None, page: int = 1, per_page: int = 50, class_filter: str = None) -> Dict[str, Any]:
        """Get unique detection groups for a specific local date with efficient group-level pagination using SQL window functions."""
        with self._connect() as conn:
            cursor = conn.cursor()

            # Base parameters
            params = [date]
            where_clauses = ["DATE(timestamp) = ?", self._GALLERY_LABEL_FILTER]
            
            if camera_ids:
                placeholders = ",".join("?" * len(camera_ids))
                where_clauses.append(f"camera_id IN ({placeholders})")
                params.extend(camera_ids)
            elif camera_id is not None:
                where_clauses.append("camera_id = ?")
                params.append(camera_id)
            
            if class_filter:
                where_clauses.append("class_name = ?")
                params.append(class_filter)
                
            where_str = " AND ".join(where_clauses)
            
            cte_sql = self._gallery_grouping_cte(where_str)
            
            # 1. Get total unique groups count for pagination metadata
            count_sql = cte_sql + "SELECT COUNT(*) FROM grouped_events WHERE rn = 1"
            cursor.execute(count_sql, params)
            total_groups = cursor.fetchone()[0]
            
            # 2. Get paginated results
            offset = (page - 1) * per_page
            
            query = cte_sql + f'''
            SELECT 
                id, camera_id, timestamp, class_name, confidence,
                bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path,
                group_count, group_key, group_type
            FROM grouped_events 
            WHERE rn = 1
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            '''
            
            # Execute main query
            query_params = params + [per_page, offset]
            cursor.execute(query, query_params)
            rows = cursor.fetchall()

            groups = []
            for r in rows:
                det_id, cam_id, timestamp, class_name, confidence, x1, y1, x2, y2, track_id, thumbnail, full_image, group_count, group_key, group_type = r
                
                groups.append({
                    "id": det_id,
                    "camera_id": cam_id,
                    "timestamp": timestamp,
                    "class_name": class_name,
                    "confidence": round(confidence, 3) if confidence else 0,
                    "bbox": [x1, y1, x2, y2],
                    "track_id": track_id,
                    "thumbnail_path": thumbnail,
                    "full_image_path": full_image,
                    "similar_count": group_count,  # Keep for backward compatibility
                    "group_count": group_count,
                    "group_type": group_type,  # 'track' or 'similar'
                    "group_key": group_key
                })

            return {
                "items": groups,
                "total": total_groups,
                "page": page,
                "per_page": per_page,
                "total_pages": (total_groups + per_page - 1) // per_page if per_page > 0 else 0
            }

    def get_detection_events_for_date_by_class(self, date: str, class_name: str, camera_id: int = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get detection events for a specific date and class"""
        with self._connect() as conn:
            cursor = conn.cursor()

            query = '''
                SELECT id, camera_id, timestamp, class_name, confidence,
                       bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path
                FROM events
                WHERE DATE(timestamp) = ? AND class_name = ?
                  AND training_label IS NULL
            '''
            params = [date, class_name]

            if camera_id is not None:
                query += ' AND camera_id = ?'
                params.append(camera_id)

            query += ' ORDER BY timestamp DESC'
            if limit is not None and limit > 0:
                query += ' LIMIT ?'
                params.append(limit)
                if offset and offset > 0:
                    query += ' OFFSET ?'
                    params.append(offset)

            cursor.execute(query, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "camera_id": row[1],
                    "timestamp": row[2],
                    "class_name": row[3],
                    "confidence": round(row[4], 3) if row[4] else 0,
                    "bbox": [row[5], row[6], row[7], row[8]],
                    "track_id": row[9],
                    "thumbnail_path": row[10]
                })

            return results

    def get_available_classes(self, hours: int = 24) -> List[str]:
        """Get list of available classes in recent detections"""
        with self._connect() as conn:
            cursor = conn.cursor()

            # Calculate timestamp for N hours ago
            from datetime import datetime, timezone, timedelta
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            cutoff_str = cutoff_time.isoformat()

            cursor.execute('''
                SELECT DISTINCT class_name
                FROM events
                WHERE timestamp >= ?
                  AND training_label IS NULL
                ORDER BY class_name
            ''', [cutoff_str])

            return [row[0] for row in cursor.fetchall()]

    def get_available_classes_for_date(self, date: str) -> List[str]:
        """Distinct class names on a calendar day."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT DISTINCT class_name
                FROM events
                WHERE DATE(timestamp) = ?
                  AND training_label IS NULL
                ORDER BY class_name
                ''',
                [date],
            )
            return [row[0] for row in cursor.fetchall()]

    def get_cameras_with_detections(
        self,
        hours: int | None = None,
        date: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Cameras that have at least one detection in the current gallery window."""
        with self._connect() as conn:
            cursor = conn.cursor()
            if date:
                cursor.execute(
                    '''
                    SELECT camera_id, COUNT(*) AS cnt
                    FROM events
                    WHERE DATE(timestamp) = ?
                      AND training_label IS NULL
                    GROUP BY camera_id
                    ORDER BY camera_id
                    ''',
                    [date],
                )
            else:
                h = hours if hours is not None else 24
                from datetime import datetime, timezone, timedelta

                cutoff = (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()
                cursor.execute(
                    '''
                    SELECT camera_id, COUNT(*) AS cnt
                    FROM events
                    WHERE timestamp >= ?
                      AND training_label IS NULL
                    GROUP BY camera_id
                    ORDER BY camera_id
                    ''',
                    [cutoff],
                )
            return [{"camera_id": row[0], "count": row[1]} for row in cursor.fetchall()]

    def get_daily_detection_counts(self, start_date: str, end_date: str) -> Dict[str, int]:
        """Get detection counts for each day in a date range"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute('''
                SELECT DATE(timestamp) as date, COUNT(*) as count
                FROM events
                WHERE DATE(timestamp) BETWEEN ? AND ?
                  AND training_label IS NULL
                GROUP BY DATE(timestamp)
                ORDER BY date
            ''', [start_date, end_date])

            return {row[0]: row[1] for row in cursor.fetchall()}

    def get_detection_counts_by_class_and_date(self, start_date: str, end_date: str) -> Dict[str, Dict[str, int]]:
        """Get detection counts by class for each day in a date range"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute('''
                SELECT DATE(timestamp) as date, class_name, COUNT(*) as count
                FROM events
                WHERE DATE(timestamp) BETWEEN ? AND ?
                GROUP BY DATE(timestamp), class_name
                ORDER BY date, class_name
            ''', [start_date, end_date])

            result = {}
            for row in cursor.fetchall():
                date, class_name, count = row
                if date not in result:
                    result[date] = {}
                result[date][class_name] = count

            return result

    def get_unique_detections(self, hours: int = 1, camera_id: int = None, camera_ids: list = None, page: int = 1, per_page: int = 50, class_filter: str = None) -> Dict[str, Any]:
        """Get unique detections by grouping similar detections by coarse position/size with SQL window functions."""
        with self._connect() as conn:
            cursor = conn.cursor()

            # Base params
            params = []
            where_clauses = [f"timestamp >= datetime('now', '-{hours} hours')", self._GALLERY_LABEL_FILTER]
            
            if camera_ids:
                placeholders = ",".join("?" * len(camera_ids))
                where_clauses.append(f"camera_id IN ({placeholders})")
                params.extend(camera_ids)
            elif camera_id is not None:
                where_clauses.append("camera_id = ?")
                params.append(camera_id)
            
            if class_filter:
                where_clauses.append("class_name = ?")
                params.append(class_filter)
                
            where_str = " AND ".join(where_clauses)
            
            cte_sql = self._gallery_grouping_cte(where_str)
            
            # 1. Get total unique groups count
            count_sql = cte_sql + "SELECT COUNT(*) FROM grouped_events WHERE rn = 1"
            cursor.execute(count_sql, params)
            total_groups = cursor.fetchone()[0]
            
            # 2. Get paginated results
            offset = (page - 1) * per_page
            
            query = cte_sql + f'''
            SELECT 
                id, camera_id, timestamp, class_name, confidence,
                bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path,
                group_count, group_key, group_type
            FROM grouped_events 
            WHERE rn = 1
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            '''
            
            query_params = params + [per_page, offset]
            cursor.execute(query, query_params)
            rows = cursor.fetchall()
            
            groups = []
            for r in rows:
                det_id, cam_id, timestamp, class_name, confidence, x1, y1, x2, y2, track_id, thumbnail, full_image, group_count, group_key, group_type = r
                
                groups.append({
                    "id": det_id,
                    "camera_id": cam_id,
                    "timestamp": timestamp,
                    "class_name": class_name,
                    "confidence": round(confidence, 3) if confidence else 0,
                    "bbox": [x1, y1, x2, y2],
                    "track_id": track_id,
                    "thumbnail_path": thumbnail,
                    "full_image_path": full_image,
                    "similar_count": group_count,  # Keep for backward compatibility
                    "group_count": group_count,
                    "group_type": group_type,  # 'track' or 'similar'
                    "group_key": group_key
                })

            return {
                "items": groups,
                "total": total_groups,
                "page": page,
                "per_page": per_page,
                "total_pages": (total_groups + per_page - 1) // per_page if per_page > 0 else 0
            }

    def get_similar_detections(self, detection_id: int, position_threshold: int = 50) -> List[Dict[str, Any]]:
        """Get all detections similar to the given detection (same position/area)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # First get the reference detection
            cursor.execute('''
                SELECT camera_id, class_name, bbox_x1, bbox_y1, bbox_x2, bbox_y2
                FROM events WHERE id = ?
            ''', [detection_id])

            ref_detection = cursor.fetchone()
            if not ref_detection:
                return []

            cam_id, class_name, x1, y1, x2, y2 = ref_detection

            # Find all similar detections
            cursor.execute('''
                SELECT id, camera_id, timestamp, class_name, confidence,
                       bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path
                FROM events
                WHERE camera_id = ? AND class_name = ?
                  AND ABS(bbox_x1 - ?) < ?
                  AND ABS(bbox_y1 - ?) < ?
                  AND ABS((bbox_x2 - bbox_x1) - ?) < ?
                  AND ABS((bbox_y2 - bbox_y1) - ?) < ?
                ORDER BY timestamp DESC
            ''', [cam_id, class_name, x1, y1, (x2-x1), (y2-y1), position_threshold, position_threshold])

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "camera_id": row[1],
                    "timestamp": row[2],
                    "class_name": row[3],
                    "confidence": round(row[4], 3) if row[4] else 0,
                    "bbox": [row[5], row[6], row[7], row[8]],
                    "track_id": row[9],
                    "thumbnail_path": row[10]
                })

            return results

    def get_detections_by_group_key(
        self,
        group_key: str,
        date_filter: str = None,
        hours: int = None,
        camera_ids: list = None,
    ) -> List[Dict[str, Any]]:
        """All detections in a gallery group (same SQL grouping as the tile badge)."""
        if not group_key or not str(group_key).strip():
            return []

        where_str, params = self._gallery_group_where(
            date_filter=date_filter,
            hours=hours,
            camera_ids=camera_ids,
        )
        cte_sql = self._gallery_grouping_cte(where_str)

        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                cte_sql
                + '''
            SELECT
                id, camera_id, timestamp, class_name, confidence,
                bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id,
                thumbnail_path, full_image_path
            FROM grouped_events
            WHERE group_key = ?
            ORDER BY timestamp ASC
            ''',
                params + [group_key],
            )
            results = [self._detection_dict_from_row(row) for row in cursor.fetchall()]

        return self._filter_resolvable_detections(results)

    def get_detection_timeline(self, group_key: tuple, hours: int = 24) -> List[Dict[str, Any]]:
        """Get all timestamps for detections in a similarity group for timeline visualization"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cam_id, class_name, pos_x, pos_y, size_w, size_h = group_key

            # Find all detections in this similarity group
            cursor.execute('''
                SELECT id, timestamp, confidence
                FROM events
                WHERE camera_id = ? AND class_name = ?
                  AND ABS(bbox_x1 - ?) < 50
                  AND ABS(bbox_y1 - ?) < 50
                  AND ABS((bbox_x2 - bbox_x1) - ?) < 50
                  AND ABS((bbox_y2 - bbox_y1) - ?) < 50
                  AND timestamp >= datetime('now', '-{} hours')
                ORDER BY timestamp ASC
            '''.format(hours), [cam_id, class_name, pos_x, pos_y, size_w, size_h])

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "timestamp": row[1],
                    "confidence": round(row[2], 3) if row[2] else 0
                })

            return results

    def get_detection_timeline_for_date(self, group_key: tuple, date: str) -> List[Dict[str, Any]]:
        """Get all timestamps for detections in a similarity group for a specific date"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cam_id, class_name, pos_x, pos_y, size_w, size_h = group_key

            # Find all detections in this similarity group for the specific date
            cursor.execute('''
                SELECT id, timestamp, confidence
                FROM events
                WHERE DATE(timestamp) = ?
                  AND camera_id = ? AND class_name = ?
                  AND ABS(bbox_x1 - ?) < 50
                  AND ABS(bbox_y1 - ?) < 50
                  AND ABS((bbox_x2 - bbox_x1) - ?) < 50
                  AND ABS((bbox_y2 - bbox_y1) - ?) < 50
                ORDER BY timestamp ASC
            ''', [date, cam_id, class_name, pos_x, pos_y, size_w, size_h])

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "timestamp": row[1],
                    "confidence": round(row[2], 3) if row[2] else 0
                })

            return results


    def delete_detection(self, detection_id):
        """Delete a detection by its ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM events WHERE id = ?", (detection_id,))
                deleted_rows = cursor.rowcount
                conn.commit()
                return deleted_rows > 0
        except Exception as e:
            logger.error(f"Error deleting detection {detection_id}: {e}")
            return False

    def delete_detections_for_camera(self, camera_id: int) -> Dict[str, Any]:
        """Delete all events + media files for one camera. Returns summary stats."""
        from basebuddy.core.services.media_paths import url_to_filesystem

        cam_id = int(camera_id)
        deleted_rows = 0
        deleted_files = 0
        freed_bytes = 0
        missing_files = 0

        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, thumbnail_path, full_image_path
                FROM events
                WHERE camera_id = ?
                """,
                (cam_id,),
            )
            rows = cursor.fetchall()

            paths_seen = set()
            for _eid, thumb, full in rows:
                for url in (thumb, full):
                    if not url or url in paths_seen:
                        continue
                    paths_seen.add(url)
                    fs = url_to_filesystem(url)
                    if not fs:
                        missing_files += 1
                        continue
                    try:
                        size = os.path.getsize(fs)
                        os.remove(fs)
                        deleted_files += 1
                        freed_bytes += size
                    except OSError:
                        missing_files += 1

            cursor.execute("DELETE FROM events WHERE camera_id = ?", (cam_id,))
            deleted_rows = cursor.rowcount
            conn.commit()

        return {
            "camera_id": cam_id,
            "deleted_events": deleted_rows,
            "deleted_files": deleted_files,
            "missing_files": missing_files,
            "freed_mb": round(freed_bytes / (1024 * 1024), 2),
            "freed_gb": round(freed_bytes / (1024**3), 3),
        }
