"""
Database operations for BaseBuddy
"""
import sqlite3
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

from .config import DB_PATH, MEDIA_BASE_DIR, MEDIA_URL_PREFIX
from .config import (
    DEDUP_ENABLE,
    DEDUP_TIME_WINDOW_S,
    DEDUP_CENTER_PX,
    DEDUP_IOU,
    DEDUP_PHASH_MAX_DIST,
    FALSE_POSITIVE_ZONES_ENABLE,
    FALSE_POSITIVE_ZONE_IOU,
)
from basebuddy.modules.db import (
    DetectionEventsMixin,
    DetectionQueriesMixin,
    TrafficMixin,
    EventSessionsMixin,
    NotificationRulesMixin,
    PersonReIDMixin,
)

class AnalyticsDB(
    DetectionEventsMixin,
    DetectionQueriesMixin,
    TrafficMixin,
    EventSessionsMixin,
    NotificationRulesMixin,
    PersonReIDMixin,
):
    """Database handler for analytics and detection data"""

    # Hide user-labeled false positives from gallery browse queries
    _GALLERY_LABEL_FILTER = "training_label IS NULL"

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_db()

    def _connect(self):
        """Open a SQLite connection configured for concurrent read/write."""
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=5000')
        except Exception:
            pass
        return conn

    def init_db(self):
        """Initialize database tables"""
        with self._connect() as conn:
            cursor = conn.cursor()

            # Create events table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id INTEGER,
                    timestamp DATETIME,
                    class_name TEXT,
                    confidence REAL,
                    bbox_x1 REAL,
                    bbox_y1 REAL,
                    bbox_x2 REAL,
                    bbox_y2 REAL,
                    track_id INTEGER,
                    thumbnail_path TEXT,
                    full_image_path TEXT
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_class_time ON events(class_name, timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_camera_time ON events(camera_id, timestamp)')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS false_positive_zones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id INTEGER NOT NULL,
                    class_name TEXT NOT NULL,
                    bbox_x1 REAL NOT NULL,
                    bbox_y1 REAL NOT NULL,
                    bbox_x2 REAL NOT NULL,
                    bbox_y2 REAL NOT NULL,
                    source_event_id INTEGER,
                    notes TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS idx_fp_zones_cam_class ON false_positive_zones(camera_id, class_name)'
            )

            # Create hourly stats table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS hourly_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id INTEGER,
                    date DATE,
                    hour INTEGER,
                    class_name TEXT,
                    count INTEGER,
                    avg_confidence REAL,
                    UNIQUE(camera_id, date, hour, class_name)
                )
            ''')

            # Create daily stats table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id INTEGER,
                    date DATE,
                    class_name TEXT,
                    count INTEGER,
                    avg_confidence REAL,
                    UNIQUE(camera_id, date, class_name)
                )
            ''')

            # Create camera_profiles table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS camera_profiles (
                    camera_id INTEGER PRIMARY KEY,
                    name TEXT,
                    purpose TEXT,
                    camera_enabled INTEGER DEFAULT 1,
                    detection_enabled INTEGER DEFAULT 1,
                    detection_fps REAL DEFAULT 1.0,
                    detection_interval_frames INTEGER DEFAULT 30,
                    detection_classes TEXT,
                    model_size TEXT DEFAULT 'nano',
                    confidence_threshold REAL DEFAULT 0.5,
                    use_gpu INTEGER DEFAULT 1,
                    recording_enabled INTEGER DEFAULT 1,
                    recording_trigger_classes TEXT,
                    recording_quality TEXT DEFAULT 'medium',
                    recording_fps REAL DEFAULT 10.0,
                    face_recognition_enabled INTEGER DEFAULT 0,
                    pose_detection_enabled INTEGER DEFAULT 0,
                    motion_detection_enabled INTEGER DEFAULT 0,
                    still_capture_enabled INTEGER DEFAULT 0,
                    still_capture_interval_seconds INTEGER DEFAULT 60,
                    audio_trigger_enabled INTEGER DEFAULT 0,
                    audio_threshold_db REAL DEFAULT -40.0,
                    max_gpu_memory_mb REAL,
                    priority INTEGER DEFAULT 5,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Add thumbnail_path column if it doesn't exist (for existing databases)
            try:
                cursor.execute("ALTER TABLE events ADD COLUMN thumbnail_path TEXT")
            except sqlite3.OperationalError:
                pass

            # Add full_image_path column if it doesn't exist (for existing databases)
            try:
                cursor.execute("ALTER TABLE events ADD COLUMN full_image_path TEXT")
            except sqlite3.OperationalError:
                pass

            try:
                cursor.execute("ALTER TABLE events ADD COLUMN region_labels TEXT")
            except sqlite3.OperationalError:
                pass

            try:
                cursor.execute("ALTER TABLE events ADD COLUMN training_label TEXT")
            except sqlite3.OperationalError:
                pass

            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_gallery_visible "
                "ON events(timestamp DESC) WHERE training_label IS NULL"
            )

            try:
                cursor.execute("ALTER TABLE events ADD COLUMN labeled_at DATETIME")
            except sqlite3.OperationalError:
                pass

            try:
                cursor.execute("ALTER TABLE events ADD COLUMN user_label TEXT")
            except sqlite3.OperationalError:
                pass

            try:
                cursor.execute("ALTER TABLE events ADD COLUMN labeled_person_id INTEGER")
            except sqlite3.OperationalError:
                pass

            try:
                cursor.execute("ALTER TABLE events ADD COLUMN corrected_class TEXT")
            except sqlite3.OperationalError:
                pass

            try:
                cursor.execute("ALTER TABLE events ADD COLUMN identity_label TEXT")
            except sqlite3.OperationalError:
                pass

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS event_sessions (
                    id TEXT PRIMARY KEY,
                    camera_id INTEGER NOT NULL,
                    class_name TEXT NOT NULL,
                    track_id INTEGER,
                    started_at REAL NOT NULL,
                    updated_at REAL,
                    ended_at REAL,
                    state TEXT NOT NULL DEFAULT 'active',
                    max_confidence REAL,
                    snapshot_path TEXT,
                    clip_path TEXT,
                    region_labels TEXT,
                    plate_text TEXT
                )
            ''')
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS idx_event_sessions_cam_time '
                'ON event_sessions(camera_id, started_at DESC)'
            )
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS idx_event_sessions_state '
                'ON event_sessions(state, started_at DESC)'
            )

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notification_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enabled INTEGER DEFAULT 1,
                    camera_id INTEGER,
                    class_name TEXT NOT NULL,
                    min_confidence REAL DEFAULT 0,
                    cooldown_s REAL DEFAULT 60,
                    notify_on TEXT DEFAULT 'start',
                    channels TEXT NOT NULL,
                    include_snapshot INTEGER DEFAULT 1,
                    include_clip INTEGER DEFAULT 0,
                    label TEXT,
                    created_at REAL
                )
            ''')
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS idx_notify_rules_cam '
                'ON notification_rules(camera_id, class_name)'
            )

            # Traffic analysis table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS traffic_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id INTEGER,
                    track_id INTEGER,
                    start_ts REAL,
                    end_ts REAL,
                    duration_s REAL,
                    start_x REAL,
                    start_y REAL,
                    end_x REAL,
                    end_y REAL,
                    distance_px REAL,
                    distance_m REAL,
                    speed_mps REAL,
                    speed_kph REAL,
                    direction_deg REAL,
                    path_json TEXT,
                    region_label TEXT,
                    class_name TEXT
                )
            ''')

            # Migrations for databases created before these columns existed
            for _col in ("region_label TEXT", "class_name TEXT"):
                try:
                    cursor.execute(f"ALTER TABLE traffic_tracks ADD COLUMN {_col}")
                except sqlite3.OperationalError:
                    pass

            # Helpful indices for traffic
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tt_cam_time ON traffic_tracks(camera_id, start_ts)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tt_time ON traffic_tracks(start_ts)')

            # --- Person Recognition Tables ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS people (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    is_unknown BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    thumbnail_path TEXT
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS person_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id INTEGER,
                    embedding BLOB,
                    camera_id INTEGER,
                    timestamp DATETIME,
                    image_path TEXT,
                    confidence REAL,
                    event_id INTEGER,
                    FOREIGN KEY(person_id) REFERENCES people(id)
                )
            ''')
            
            # Add event_id column if it doesn't exist
            try:
                cursor.execute("ALTER TABLE person_embeddings ADD COLUMN event_id INTEGER")
            except sqlite3.OperationalError:
                pass
            
            # Create face_scan_progress table to track which events have been scanned
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS face_scan_progress (
                    event_id INTEGER PRIMARY KEY,
                    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(event_id) REFERENCES events(id)
                )
            ''')
            
            # Create index for faster lookups
            try:
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_face_scan_event ON face_scan_progress(event_id)')
            except Exception:
                pass

            # (Removed dead schema: poses, social_accounts, social_posts,
            # scheduled_posts — no code writes or reads these tables.)

            try:
                from basebuddy.plugins.home_scenes.db import init_scene_tables
                init_scene_tables(conn)
            except Exception:
                pass

            conn.commit()
