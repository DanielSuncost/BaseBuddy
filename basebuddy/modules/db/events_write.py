"""Detection event ingestion: dedup, false-positive zones, labeling, stats.

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


class DetectionEventsMixin:
    def store_event(self, camera_id: int, detections, frame=None, class_name_func=None, timestamp: datetime = None, region_labels=None):
        """Store detection events in database with retry logic for database locks"""
        if timestamp is None:
            # Use current time in UTC
            timestamp = datetime.now(timezone.utc)

        # Retry logic for database locked errors
        max_retries = 5
        retry_delay = 0.1  # Start with 100ms
        
        for attempt in range(max_retries):
            try:
                conn = self._connect()  # Use proper connection with WAL mode and timeout
                cursor = conn.cursor()
                session_hooks = []

                fp_zones = []
                if FALSE_POSITIVE_ZONES_ENABLE:
                    try:
                        fp_zones = self._load_false_positive_zones_for_camera(cursor, camera_id)
                    except Exception:
                        fp_zones = []

                for i in range(len(detections)):
                    # Get class name
                    class_id = int(detections.class_id[i])
                    if class_name_func:
                        class_name = class_name_func(class_id)
                    else:
                        class_name = f"class_{class_id}"

                    # Get confidence
                    confidence = float(detections.confidence[i])

                    # Get bounding box
                    bbox = detections.xyxy[i]
                    x1, y1, x2, y2 = map(float, bbox)

                    # Get track ID if available
                    track_id = None
                    if hasattr(detections, 'tracker_id') and detections.tracker_id is not None:
                        track_id = int(detections.tracker_id[i]) if detections.tracker_id[i] is not None else None

                    det_region_labels = None
                    if region_labels and i < len(region_labels):
                        det_region_labels = region_labels[i]
                    elif hasattr(detections, 'data') and detections.data.get('region_labels'):
                        rl = detections.data['region_labels']
                        if i < len(rl):
                            det_region_labels = rl[i]

                    # Deduplicate near-identical detections in recent window
                    try:
                        if DEDUP_ENABLE:
                            if self._is_duplicate_event(cursor, camera_id, class_name, x1, y1, x2, y2, frame, track_id):
                                continue
                    except Exception as _e:
                        # Fail open if any issue
                        pass

                    if FALSE_POSITIVE_ZONES_ENABLE and fp_zones:
                        try:
                            if self._matches_false_positive_zone(fp_zones, class_name, x1, y1, x2, y2):
                                continue
                        except Exception:
                            pass

                    # Extract and save images (thumbnail + full-size padded crop)
                    thumbnail_path = None
                    full_image_path = None
                    if frame is not None:
                        thumbnail_path, full_image_path = self._save_detection_images(frame, x1, y1, x2, y2, camera_id, timestamp, i)

                    cursor.execute('''
                        INSERT INTO events (camera_id, timestamp, class_name, confidence,
                                          bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path, region_labels)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (camera_id, timestamp, class_name, confidence, x1, y1, x2, y2, track_id, thumbnail_path, full_image_path, det_region_labels))

                    # Update hourly and daily stats
                    self._update_hourly_stats(cursor, camera_id, timestamp, class_name, confidence)
                    self._update_daily_stats(cursor, camera_id, timestamp, class_name, confidence)

                    session_hooks.append({
                        'camera_id': camera_id,
                        'class_name': class_name,
                        'confidence': confidence,
                        'track_id': track_id,
                        'thumbnail_path': thumbnail_path,
                        'full_image_path': full_image_path,
                        'region_labels': det_region_labels,
                    })

                conn.commit()
                conn.close()
                for hook in session_hooks:
                    try:
                        from basebuddy.core.services.event_session_service import get_event_session_service
                        get_event_session_service().on_detection_stored(
                            hook['camera_id'],
                            hook['class_name'],
                            hook['confidence'],
                            hook['track_id'],
                            hook['thumbnail_path'],
                            hook['region_labels'],
                            hook.get('full_image_path'),
                        )
                    except Exception:
                        pass
                    try:
                        from basebuddy.core.services.region_notifications import get_region_notification_service
                        get_region_notification_service().flush_pending_with_media(
                            hook['camera_id'],
                            hook['class_name'],
                            hook.get('thumbnail_path'),
                            hook.get('full_image_path'),
                        )
                    except Exception:
                        pass
                return  # Success! Exit the function
                
            except sqlite3.OperationalError as e:
                if 'locked' in str(e).lower() and attempt < max_retries - 1:
                    # Database is locked, retry after a delay
                    import time
                    time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue
                else:
                    # Max retries exceeded or different error
                    try:
                        conn.close()
                    except Exception:
                        pass
                    raise
            except Exception as e:
                try:
                    conn.close()
                except Exception:
                    pass
                raise

    # ---------- Deduplication helpers ----------
    @staticmethod
    def _bbox_iou(a, b) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        ua = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1) + max(0.0, bx2 - bx1) * max(0.0, by2 - by1) - inter
        return float(inter / ua) if ua > 0 else 0.0

    def _load_false_positive_zones_for_camera(self, cursor, camera_id: int) -> List[Dict[str, Any]]:
        cursor.execute(
            """
            SELECT class_name, bbox_x1, bbox_y1, bbox_x2, bbox_y2
            FROM false_positive_zones
            WHERE camera_id = ?
            """,
            (camera_id,),
        )
        zones = []
        for row in cursor.fetchall():
            zones.append(
                {
                    "class_name": row[0],
                    "bbox": (float(row[1]), float(row[2]), float(row[3]), float(row[4])),
                }
            )
        return zones

    def _matches_false_positive_zone(
        self, zones: List[Dict[str, Any]], class_name: str, x1: float, y1: float, x2: float, y2: float
    ) -> bool:
        box = (x1, y1, x2, y2)
        for z in zones:
            if z["class_name"] != class_name:
                continue
            if self._bbox_iou(box, z["bbox"]) >= FALSE_POSITIVE_ZONE_IOU:
                return True
        return False

    def get_event_by_id(self, event_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, camera_id, timestamp, class_name, confidence,
                       bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id,
                       thumbnail_path, full_image_path,
                       training_label, user_label, labeled_person_id, corrected_class, identity_label
                FROM events WHERE id = ?
                """,
                (event_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "camera_id": row[1],
                "timestamp": row[2],
                "class_name": row[3],
                "confidence": row[4],
                "bbox_x1": float(row[5]),
                "bbox_y1": float(row[6]),
                "bbox_x2": float(row[7]),
                "bbox_y2": float(row[8]),
                "track_id": row[9],
                "thumbnail_path": row[10],
                "full_image_path": row[11],
                "training_label": row[12],
                "user_label": row[13],
                "labeled_person_id": row[14],
                "corrected_class": row[15],
                "identity_label": row[16],
            }

    def add_false_positive_zone(
        self,
        camera_id: int,
        class_name: str,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        source_event_id: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> Optional[int]:
        """Save an ignore zone. Returns zone id (new or existing near-duplicate)."""
        new_box = (float(x1), float(y1), float(x2), float(y2))
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, bbox_x1, bbox_y1, bbox_x2, bbox_y2
                FROM false_positive_zones
                WHERE camera_id = ? AND class_name = ?
                """,
                (camera_id, class_name),
            )
            for row in cursor.fetchall():
                if self._bbox_iou(new_box, (float(row[1]), float(row[2]), float(row[3]), float(row[4]))) >= 0.9:
                    return int(row[0])
            cursor.execute(
                """
                INSERT INTO false_positive_zones
                (camera_id, class_name, bbox_x1, bbox_y1, bbox_x2, bbox_y2, source_event_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (camera_id, class_name, x1, y1, x2, y2, source_event_id, notes),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def add_false_positive_zone_from_event(
        self, event_id: int, notes: Optional[str] = None
    ) -> Optional[int]:
        ev = self.get_event_by_id(event_id)
        if not ev:
            return None
        return self.add_false_positive_zone(
            ev["camera_id"],
            ev["class_name"],
            ev["bbox_x1"],
            ev["bbox_y1"],
            ev["bbox_x2"],
            ev["bbox_y2"],
            source_event_id=event_id,
            notes=notes,
        )

    def list_false_positive_zones(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, camera_id, class_name, bbox_x1, bbox_y1, bbox_x2, bbox_y2,
                       source_event_id, notes, created_at
                FROM false_positive_zones
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row[0],
                    "camera_id": row[1],
                    "class_name": row[2],
                    "bbox_x1": row[3],
                    "bbox_y1": row[4],
                    "bbox_x2": row[5],
                    "bbox_y2": row[6],
                    "source_event_id": row[7],
                    "notes": row[8],
                    "created_at": row[9],
                }
            )
        return out

    def delete_false_positive_zone(self, zone_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM false_positive_zones WHERE id = ?", (zone_id,))
            conn.commit()
            return cursor.rowcount > 0

    def mark_detections_false_positive(self, event_ids: List[int]) -> int:
        """Label detections as false positives for training export; hides them from gallery."""
        ids = [int(i) for i in event_ids if i is not None]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE events
                SET training_label = 'false_positive', labeled_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                  AND training_label IS NULL
                """,
                ids,
            )
            conn.commit()
            return cursor.rowcount

    def get_or_create_named_person(self, name: str) -> Optional[int]:
        name = (name or "").strip()
        if not name:
            return None
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM people WHERE name = ? AND is_unknown = 0",
                (name,),
            )
            row = cursor.fetchone()
            if row:
                return int(row[0])
            cursor.execute(
                "INSERT INTO people (name, is_unknown) VALUES (?, 0)",
                (name,),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def label_detection(
        self,
        event_id: int,
        training_label: str = "verified",
        user_label: Optional[str] = None,
        labeled_person_id: Optional[int] = None,
        corrected_class: Optional[str] = None,
        identity_label: Optional[str] = None,
    ) -> bool:
        """Apply a training label to a detection (keeps it visible unless false_positive)."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE events
                SET training_label = ?,
                    user_label = COALESCE(?, user_label),
                    labeled_person_id = COALESCE(?, labeled_person_id),
                    corrected_class = COALESCE(?, corrected_class),
                    identity_label = COALESCE(?, identity_label),
                    labeled_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    training_label,
                    user_label,
                    labeled_person_id,
                    corrected_class,
                    identity_label,
                    int(event_id),
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_events_for_export(self, hours: int = 168, limit: int = 5000) -> List[Dict[str, Any]]:
        """Recent detection events with paths and boxes for training export."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, camera_id, timestamp, class_name, confidence,
                       bbox_x1, bbox_y1, bbox_x2, bbox_y2,
                       thumbnail_path, full_image_path, training_label, labeled_at,
                       user_label, labeled_person_id, corrected_class, identity_label
                FROM events
                WHERE timestamp >= datetime('now', ?)
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (f'-{int(hours)} hours', limit),
            )
            rows = cursor.fetchall()
        out = []
        for row in rows:
            out.append({
                'id': row[0],
                'camera_id': row[1],
                'timestamp': row[2],
                'class_name': row[3],
                'confidence': row[4],
                'bbox_x1': row[5],
                'bbox_y1': row[6],
                'bbox_x2': row[7],
                'bbox_y2': row[8],
                'thumbnail_path': row[9],
                'full_image_path': row[10],
                'training_label': row[11],
                'labeled_at': row[12],
                'user_label': row[13],
                'labeled_person_id': row[14],
                'corrected_class': row[15],
                'identity_label': row[16],
            })
        return out

    @staticmethod
    def _compute_phash(image) -> list:
        """Compute a 64-bit perceptual hash as list[bool] for an image (numpy array, BGR)."""
        try:
            import cv2
            import numpy as np
            if image is None or image.size == 0:
                return []
            # Convert to grayscale and resize to 32x32
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
            # Compute DCT
            small = small.astype(np.float32)
            dct = cv2.dct(small)
            # Use top-left 8x8
            dct_low = dct[:8, :8]
            # Exclude DC component for mean
            dct_flat = dct_low.flatten()
            mean_val = float((dct_flat[1:].mean()) if dct_flat.size > 1 else dct_flat.mean())
            bits = (dct_low >= mean_val).astype(np.uint8).flatten()
            return bits.tolist()
        except Exception:
            return []

    @staticmethod
    def _phash_distance(h1: list, h2: list) -> int:
        if not h1 or not h2 or len(h1) != len(h2):
            return 1 << 30
        return sum(1 for a, b in zip(h1, h2) if a != b)

    def _is_duplicate_event(self, cursor, camera_id: int, class_name: str, x1: float, y1: float, x2: float, y2: float, frame, track_id: int | None) -> bool:
        """Return True if this detection is a near-duplicate of a recent one.

        Criteria:
        - Within DEDUP_TIME_WINDOW_S
        - Same camera and class
        - Spatially close (IoU >= DEDUP_IOU or center distance <= DEDUP_CENTER_PX)
        - And visually similar (pHash distance <= DEDUP_PHASH_MAX_DIST) if images available
        - If track_id matches recently and spatially close, skip without image similarity
        """
        try:
            # Fetch recent candidates
            base_query = f'''
                SELECT id, timestamp, bbox_x1, bbox_y1, bbox_x2, bbox_y2, track_id, thumbnail_path, full_image_path
                FROM events
                WHERE camera_id = ? AND class_name = ?
                  AND timestamp >= datetime('now', '-{DEDUP_TIME_WINDOW_S} seconds')
                ORDER BY timestamp DESC
                LIMIT 50
            '''
            params = [camera_id, class_name]
            cursor.execute(base_query, params)
            rows = cursor.fetchall()
            if not rows:
                return False

            # Prepare current bbox and center
            cur_bbox = (float(x1), float(y1), float(x2), float(y2))
            cur_cx = (x1 + x2) / 2.0
            cur_cy = (y1 + y2) / 2.0

            # Optionally compute pHash of current crop
            cur_phash = []
            if frame is not None:
                try:
                    h, w = frame.shape[:2]
                    xi1 = max(0, min(int(x1), w - 1))
                    yi1 = max(0, min(int(y1), h - 1))
                    xi2 = max(0, min(int(x2), w))
                    yi2 = max(0, min(int(y2), h))
                    if xi2 > xi1 and yi2 > yi1:
                        crop = frame[yi1:yi2, xi1:xi2]
                        cur_phash = self._compute_phash(crop)
                except Exception:
                    cur_phash = []

            for r in rows:
                _id, _ts, bx1, by1, bx2, by2, trk, thumb_path, full_path = r
                cand_bbox = (float(bx1), float(by1), float(bx2), float(by2))
                cand_cx = (cand_bbox[0] + cand_bbox[2]) / 2.0
                cand_cy = (cand_bbox[1] + cand_bbox[3]) / 2.0

                center_dist = ((cur_cx - cand_cx) ** 2 + (cur_cy - cand_cy) ** 2) ** 0.5
                iou_val = self._bbox_iou(cur_bbox, cand_bbox)

                spatial_close = (iou_val >= DEDUP_IOU) or (center_dist <= DEDUP_CENTER_PX)
                if not spatial_close:
                    continue

                # If track_id matches and spatially close, consider duplicate immediately
                if track_id is not None and trk is not None and int(track_id) == int(trk):
                    return True

                # Visual similarity check if we have images
                try:
                    cand_img = None
                    import cv2
                    # Prefer full image crop if available
                    disk_path = None
                    if full_path and isinstance(full_path, str):
                        # MEDIA_URL_PREFIX is a URL path prefix; map to filesystem by MEDIA_BASE_DIR
                        # full_path is like "/media/detections/xxx.jpg"; replace prefix with MEDIA_BASE_DIR
                        if full_path.startswith(MEDIA_URL_PREFIX):
                            disk_path = os.path.join(MEDIA_BASE_DIR, full_path[len(MEDIA_URL_PREFIX):].lstrip('/'))
                    if not disk_path and thumb_path and isinstance(thumb_path, str):
                        if thumb_path.startswith(MEDIA_URL_PREFIX):
                            disk_path = os.path.join(MEDIA_BASE_DIR, thumb_path[len(MEDIA_URL_PREFIX):].lstrip('/'))
                    if disk_path and os.path.exists(disk_path):
                        cand_img = cv2.imread(disk_path, cv2.IMREAD_COLOR)
                    cand_phash = self._compute_phash(cand_img) if cand_img is not None else []
                    if cur_phash and cand_phash:
                        if self._phash_distance(cur_phash, cand_phash) <= DEDUP_PHASH_MAX_DIST:
                            return True
                except Exception:
                    # If any error in image comparison, fallback to spatial-only decision
                    return True if spatial_close and iou_val >= DEDUP_IOU else False

            return False
        except Exception:
            return False

    def _save_detection_images(self, frame, x1, y1, x2, y2, camera_id, timestamp, detection_idx):
        """Save thumbnail and full-size padded crop of the detection"""
        try:
            import cv2

            # Extract bounding box region from original frame
            pad = 200
            x1_p = int(x1) - pad
            y1_p = int(y1) - pad
            x2_p = int(x2) + pad
            y2_p = int(y2) + pad

            # Ensure coordinates are within frame bounds
            h, w = frame.shape[:2]
            x1_int = max(0, min(x1_p, w-1))
            y1_int = max(0, min(y1_p, h-1))
            x2_int = max(0, min(x2_p, w))
            y2_int = max(0, min(y2_p, h))

            if x2_int > x1_int and y2_int > y1_int:
                # Extract the detection region
                detection_crop = frame[y1_int:y2_int, x1_int:x2_int]

                # Check if crop is valid
                if detection_crop.size > 0:
                    # Get crop dimensions
                    crop_h, crop_w = detection_crop.shape[:2]

                    # Calculate resize dimensions to fit in 80x80 while maintaining aspect ratio
                    max_size = 80
                    if crop_w > crop_h:
                        # Wider than tall - fit to width
                        new_w = max_size
                        new_h = int(crop_h * max_size / crop_w)
                    else:
                        # Taller than wide - fit to height
                        new_h = max_size
                        new_w = int(crop_w * max_size / crop_h)

                    # Ensure minimum size
                    new_w = max(20, new_w)
                    new_h = max(20, new_h)

                    # Resize maintaining aspect ratio
                    thumbnail = cv2.resize(detection_crop, (new_w, new_h))

                    # Save thumbnail to external media base
                    thumbs_dir = os.path.join(MEDIA_BASE_DIR, "thumbnails")
                    os.makedirs(thumbs_dir, exist_ok=True)

                    # Save thumbnail
                    thumb_filename = f"thumb_{camera_id}_{int(timestamp.timestamp())}_{detection_idx}.jpg"
                    thumbnail_path_fs = os.path.join(thumbs_dir, thumb_filename)
                    thumb_success = cv2.imwrite(thumbnail_path_fs, thumbnail)
                    
                    if not thumb_success:
                        logger.error(f"Failed to write thumbnail: {thumbnail_path_fs}")
                        return None, None

                    # Save full-size crop at high quality
                    full_dir = os.path.join(MEDIA_BASE_DIR, "detections")
                    os.makedirs(full_dir, exist_ok=True)
                    full_filename = f"det_{camera_id}_{int(timestamp.timestamp())}_{detection_idx}.jpg"
                    full_path = os.path.join(full_dir, full_filename)
                    # Write JPEG at high quality (95) to preserve detail
                    try:
                        full_success = cv2.imwrite(full_path, detection_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                    except Exception:
                        full_success = cv2.imwrite(full_path, detection_crop)
                    
                    if not full_success:
                        logger.error(f"Failed to write full detection: {full_path}")
                        # Clean up thumbnail if full write failed
                        try:
                            os.remove(thumbnail_path_fs)
                        except Exception:
                            pass
                        return None, None

                    return f"{MEDIA_URL_PREFIX}/thumbnails/{thumb_filename}", f"{MEDIA_URL_PREFIX}/detections/{full_filename}"
        except Exception as e:
            logger.error(f"Error saving thumbnail: {e}")

        return None, None

    def _update_hourly_stats(self, cursor, camera_id: int, timestamp: datetime, class_name: str, confidence: float):
        """Update hourly statistics"""
        date_str = timestamp.strftime("%Y-%m-%d")
        hour = timestamp.hour

        # Check if record exists
        cursor.execute('''
            SELECT id, count, avg_confidence FROM hourly_stats
            WHERE camera_id = ? AND date = ? AND hour = ? AND class_name = ?
        ''', (camera_id, date_str, hour, class_name))

        result = cursor.fetchone()

        if result:
            # Update existing record
            current_count = result[1]
            current_avg = result[2]
            new_count = current_count + 1
            new_avg = (current_avg * current_count + confidence) / new_count

            cursor.execute('''
                UPDATE hourly_stats SET count = ?, avg_confidence = ?
                WHERE id = ?
            ''', (new_count, new_avg, result[0]))
        else:
            # Insert new record
            cursor.execute('''
                INSERT INTO hourly_stats (camera_id, date, hour, class_name, count, avg_confidence)
                VALUES (?, ?, ?, ?, 1, ?)
            ''', (camera_id, date_str, hour, class_name, confidence))

    def _update_daily_stats(self, cursor, camera_id: int, timestamp: datetime, class_name: str, confidence: float):
        """Update daily statistics"""
        date_str = timestamp.strftime("%Y-%m-%d")

        # Check if record exists
        cursor.execute('''
            SELECT id, count, avg_confidence FROM daily_stats
            WHERE camera_id = ? AND date = ? AND class_name = ?
        ''', (camera_id, date_str, class_name))

        result = cursor.fetchone()

        if result:
            # Update existing record
            current_count = result[1]
            current_avg = result[2]
            new_count = current_count + 1
            new_avg = (current_avg * current_count + confidence) / new_count

            cursor.execute('''
                UPDATE daily_stats SET count = ?, avg_confidence = ?
                WHERE id = ?
            ''', (new_count, new_avg, result[0]))
        else:
            # Insert new record
            cursor.execute('''
                INSERT INTO daily_stats (camera_id, date, class_name, count, avg_confidence)
                VALUES (?, ?, ?, 1, ?)
            ''', (camera_id, date_str, class_name, confidence))
