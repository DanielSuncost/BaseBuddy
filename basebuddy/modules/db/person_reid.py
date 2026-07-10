"""Person re-identification embedding storage.

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


class PersonReIDMixin:
    # ---------------- Person Re-ID Helpers -----------------
    def save_person_embedding(self, camera_id: int, embedding: bytes, image_path: str, timestamp: datetime = None):
        """Save a new person embedding and try to match with existing people"""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
            
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # For now, just save as unknown person or create new one
            # real matching logic would happen in the detection loop or here
            # For simplicity, we create a new "Unknown" person entry for every distinct track, 
            # but ideally we want to cluster them.
            
            # Simple placeholder: Create a new person for this embedding
            cursor.execute('INSERT INTO people (name, is_unknown, thumbnail_path) VALUES (?, 1, ?)', 
                          (f"Person_{int(timestamp.timestamp())}", image_path))
            person_id = cursor.lastrowid
            
            cursor.execute('''
                INSERT INTO person_embeddings (person_id, embedding, camera_id, timestamp, image_path, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (person_id, embedding, camera_id, timestamp, image_path, 1.0))
            
            conn.commit()
            return person_id
            
    def get_people(self, limit: int = 50):
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM people ORDER BY created_at DESC LIMIT ?', (limit,))
            return [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]
