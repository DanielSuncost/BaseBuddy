
"""
Configuration management for BaseBuddy
"""
import os
import json
from typing import Dict, Any, List

def DEF(key: str, default: Any = None) -> Any:
    """Get environment variable with fallback to default"""
    return os.environ.get(key, default)

# --- Configuration support ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

def _config_txt_path() -> str:
    try:
        from basebuddy.core.paths import get_repo_root
        return os.path.join(get_repo_root(), "config.txt")
    except Exception:
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.txt")

# Load config.txt if it exists (fallback for systems without dotenv)
def load_config_file():
    """Load configuration from config.txt file"""
    config_path = _config_txt_path()

    try:
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                # Remove comments from the line (anything after #)
                if '#' in line:
                    line = line.split('#', 1)[0].strip()
                # Parse export statements: export KEY="value" or KEY="value"
                if line.startswith('export '):
                    line = line[7:]  # Remove 'export '
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    # Remove surrounding quotes if present
                    if (value.startswith('"') and value.endswith('"')) or \
                        (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    os.environ[key] = value
    except Exception:
        pass

def env_live(key: str, default: Any = None) -> Any:
    """Read config after load_config_file — prefers current os.environ."""
    val = os.environ.get(key)
    if val is not None and val != "":
        return val
    return globals().get(key, default)


load_config_file()

from basebuddy.core.paths import abs_data_path, get_repo_root
import logging

logger = logging.getLogger(__name__)

# Core configuration
# Load cameras dynamically - support up to 20 cameras
CAM_URLS = []
for i in range(1, 21):  # Support up to 20 cameras
    url = DEF(f"CAM{i}", "")
    CAM_URLS.append(url)

# Flask configuration
HOST = DEF("HOST", "0.0.0.0")
PORT = int(DEF("PORT", "5000"))

# Recording configuration
RECORD_ROOT = DEF("RECORD_ROOT", "recordings")
SEG_MINUTES = int(DEF("SEG_MINUTES", "10"))
RETENTION_DAYS = int(DEF("RETENTION_DAYS", "7"))
RETENTION_SCAN_S = int(DEF("RETENTION_SCAN_S", "300"))

# Plant tracking / segmentation configuration
PLANT_TRACKING_ROOT = DEF("PLANT_TRACKING_ROOT", "plant_tracking")

# Backup configuration
BACKUP_ENABLED = DEF("BACKUP_ENABLED", "false").lower() == "true"
BACKUP_DRIVE_PATH = DEF("BACKUP_DRIVE_PATH", "")
BACKUP_FOLDER = DEF("BACKUP_FOLDER", "backup_surveillance_recordings")
BACKUP_INTERVAL_HOURS = int(DEF("BACKUP_INTERVAL_HOURS", "1"))
BACKUP_MAX_AGE_HOURS = int(DEF("BACKUP_MAX_AGE_HOURS", "24"))

# Archive configuration (weekly move-to-HRL + local cleanup)
ARCHIVE_ENABLED = DEF("ARCHIVE_ENABLED", "false").lower() == "true"
ARCHIVE_DRIVE_PATH = DEF("ARCHIVE_DRIVE_PATH", "")
ARCHIVE_FOLDER = DEF("ARCHIVE_FOLDER", "basebuddy_archive")
# How often to scan for files to move off the local disk (default: daily)
ARCHIVE_INTERVAL_DAYS = int(DEF("ARCHIVE_INTERVAL_DAYS", "1"))
ARCHIVE_MIN_AGE_DAYS = int(DEF("ARCHIVE_MIN_AGE_DAYS", "2"))

# Soft quota (GiB) for sum of local archive source dirs; extra archive runs when exceeded (0 = off)
try:
    _sq = DEF("STORAGE_QUOTA_GB", "0")
    STORAGE_QUOTA_GB = float(_sq) if _sq not in (None, "", "null") else 0.0
except (TypeError, ValueError):
    STORAGE_QUOTA_GB = 0.0

# When free disk falls below this many GiB, retention deletes oldest detections/stills
# even if they are still inside the age window (0 = disable disk-pressure eviction).
try:
    _df = DEF("DISK_FREE_MIN_GB", "20")
    DISK_FREE_MIN_GB = float(_df) if _df not in (None, "", "null") else 20.0
except (TypeError, ValueError):
    DISK_FREE_MIN_GB = 20.0

# Per-service retention (JSON). See Storage settings UI.
RETENTION_POLICY_JSON = DEF("RETENTION_POLICY", "")
RETENTION_SCAN_HOURS = int(DEF("RETENTION_SCAN_HOURS", "1"))

# Remote object storage (BYO S3 / R2 / B2). Premium managed cloud uses basebuddy_premium.
REMOTE_STORAGE_ENABLED = DEF("REMOTE_STORAGE_ENABLED", "false").lower() == "true"
REMOTE_STORAGE_PROVIDER = DEF("REMOTE_STORAGE_PROVIDER", "s3")  # s3 | r2 | b2
REMOTE_BUCKET = DEF("REMOTE_BUCKET", "")
REMOTE_REGION = DEF("REMOTE_REGION", "auto")
REMOTE_ENDPOINT = DEF("REMOTE_ENDPOINT", "")
REMOTE_PREFIX = DEF("REMOTE_PREFIX", "basebuddy")
REMOTE_ACCESS_KEY = DEF("REMOTE_ACCESS_KEY", "")
REMOTE_SECRET_KEY = DEF("REMOTE_SECRET_KEY", "")

# BaseBuddy Cloud (hosted premium API — requires basebuddy_premium package)
BASEBUDDY_MANAGED_CLOUD_ENABLED = DEF("BASEBUDDY_MANAGED_CLOUD_ENABLED", "false").lower() == "true"
BASEBUDDY_CLOUD_API_URL = DEF("BASEBUDDY_CLOUD_API_URL", "")
BASEBUDDY_CLOUD_API_KEY = DEF("BASEBUDDY_CLOUD_API_KEY", "")

# Buffer configuration
# Only the most recent frame is ever read from this buffer (live streaming),
# so keep it tiny — each raw 1080p frame is ~6MB.
BUFFER_MAX_FRAMES = int(DEF("BUFFER_MAX_FRAMES", "3"))
JPEG_QUALITY = int(DEF("JPEG_QUALITY", "80"))

# Detection configuration
AI_MODEL = DEF("AI_MODEL", "yolov8n.pt")  # Legacy fallback model
DAY_MODEL = DEF("DAY_MODEL", "yolov8s.pt")  # Daytime model (better accuracy)
NIGHT_MODEL = DEF("NIGHT_MODEL", "yolov8m.pt")  # Nighttime model (better low-light)
TRACKER_TYPE = DEF("TRACKER_TYPE", "bytetrack")
DETECTION_ENABLED = DEF("DETECTION_ENABLED", "true").lower() == "true"
AI_CONF = float(DEF("AI_CONF", "0.35"))  # Legacy confidence threshold
DAY_CONF = float(DEF("DAY_CONF", "0.4"))  # Daytime confidence threshold
NIGHT_CONF = float(DEF("NIGHT_CONF", "0.25"))  # Nighttime confidence threshold
AI_FPS = int(DEF("AI_FPS", "3"))
ADAPTIVE_MODE = DEF("ADAPTIVE_MODE", "true").lower() == "true"  # Enable adaptive model switching
DARK_THRESHOLD = float(DEF("DARK_THRESHOLD", "0.3"))  # Brightness threshold for night mode
NIGHT_ENHANCEMENT = DEF("NIGHT_ENHANCEMENT", "true").lower() == "true"  # Enable night image enhancement
FRAME_ACCUMULATION = DEF("FRAME_ACCUMULATION", "false").lower() == "true"  # Enable frame accumulation for night
ACCUMULATION_FRAMES = int(DEF("ACCUMULATION_FRAMES", "3"))  # Number of frames to accumulate
NIGHT_GAMMA = float(DEF("NIGHT_GAMMA", "0.8"))  # Gamma correction for night enhancement
NIGHT_CLAHE_CLIP = float(DEF("NIGHT_CLAHE_CLIP", "3.0"))  # CLAHE clip limit for night enhancement

DISPLAY_MAX_WIDTH = int(DEF("DISPLAY_MAX_WIDTH", "960"))
DISPLAY_TARGET_FPS = int(DEF("DISPLAY_TARGET_FPS", "12"))
DETECTION_IDLE_FPS = float(DEF("DETECTION_IDLE_FPS", "0.5"))
DETECTION_ACTIVE_FPS = float(DEF("DETECTION_ACTIVE_FPS", "4"))
DETECTION_ACTIVE_SECS = float(DEF("DETECTION_ACTIVE_SECS", "12"))


def _int_env(key: str, fallback: int) -> int:
    """Parse integer environment variable safely"""
    raw = DEF(key, "")
    if raw in (None, "", "null"):
        return fallback
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


MAX_ACTIVE_CAMERAS = _int_env("MAX_ACTIVE_CAMERAS", 0)
BASEBUDDY_SAFE_MODE = DEF("BASEBUDDY_SAFE_MODE", "0").lower() in ("1", "true", "yes", "on")

if BASEBUDDY_SAFE_MODE:
    # Safe mode prioritizes keeping the workstation responsive
    if MAX_ACTIVE_CAMERAS == 0:
        MAX_ACTIVE_CAMERAS = 2
    AI_FPS = min(AI_FPS, 1)
    ADAPTIVE_MODE = False
    logger.info(f"BaseBuddy SAFE MODE enabled: limiting to {MAX_ACTIVE_CAMERAS} camera(s), AI_FPS={AI_FPS}")

# Processing architecture
MULTIPROC_DETECTION = DEF("MULTIPROC_DETECTION", "false").lower() == "true"

# Recording mode: continuous | motion | detection | off
RECORDING_MODE = DEF("RECORDING_MODE", "continuous").lower()
EVENT_CLIP_PRE_S = float(DEF("EVENT_CLIP_PRE_S", "5"))
EVENT_CLIP_POST_S = float(DEF("EVENT_CLIP_POST_S", "5"))

# FFmpeg hardware acceleration for decode (vaapi, cuda, qsv, or empty=software)
FFMPEG_HWACCEL = DEF("FFMPEG_HWACCEL", "").strip().lower()

# Model inference backend: pt | tensorrt | openvino
INFERENCE_BACKEND = DEF("INFERENCE_BACKEND", "pt").lower()

# Integrations / notifications
NOTIFY_ENABLED = DEF("NOTIFY_ENABLED", "true").lower() == "true"
NOTIFY_WEBHOOK_URL = DEF("NOTIFY_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = DEF("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = DEF("TELEGRAM_CHAT_ID", "")
PUSHOVER_USER_KEY = DEF("PUSHOVER_USER_KEY", "")
PUSHOVER_API_TOKEN = DEF("PUSHOVER_API_TOKEN", "")

# Email (SMTP)
SMTP_HOST = DEF("SMTP_HOST", "")
SMTP_PORT = int(DEF("SMTP_PORT", "587"))
SMTP_USER = DEF("SMTP_USER", "")
SMTP_PASSWORD = DEF("SMTP_PASSWORD", "")
SMTP_FROM = DEF("SMTP_FROM", "")
SMTP_TO = DEF("SMTP_TO", "")
SMTP_USE_TLS = DEF("SMTP_USE_TLS", "true").lower() == "true"

# SMS (Twilio)
TWILIO_ACCOUNT_SID = DEF("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = DEF("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = DEF("TWILIO_FROM_NUMBER", "")
TWILIO_TO_NUMBER = DEF("TWILIO_TO_NUMBER", "")

# Public URL for links in SMS/Pushover when not uploading files (e.g. https://cam.example.com)
ONBOARDING_COMPLETE = DEF("ONBOARDING_COMPLETE", "false").lower() == "true"

# Plant health vision API (OSS — any OpenAI-compatible vision endpoint)
PLANT_VISION_API_URL = DEF("PLANT_VISION_API_URL", "")
PLANT_VISION_API_KEY = DEF("PLANT_VISION_API_KEY", "")
PLANT_VISION_MODEL = DEF("PLANT_VISION_MODEL", "gpt-4o-mini")

NOTIFY_PUBLIC_BASE_URL = DEF("NOTIFY_PUBLIC_BASE_URL", "")
NOTIFY_FALLBACK_GLOBAL = DEF("NOTIFY_FALLBACK_GLOBAL", "true").lower() == "true"
NOTIFY_INCLUDE_CLIP_DEFAULT = DEF("NOTIFY_INCLUDE_CLIP_DEFAULT", "true").lower() == "true"

# MQTT (Home Assistant, Node-RED)
MQTT_ENABLED = DEF("MQTT_ENABLED", "false").lower() == "true"
MQTT_HOST = DEF("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(DEF("MQTT_PORT", "1883"))
MQTT_USERNAME = DEF("MQTT_USERNAME", "")
MQTT_PASSWORD = DEF("MQTT_PASSWORD", "")
MQTT_TOPIC_PREFIX = DEF("MQTT_TOPIC_PREFIX", "basebuddy")
MQTT_CLIENT_ID = DEF("MQTT_CLIENT_ID", "basebuddy")

# LPR plugin
LPR_ENABLED = DEF("LPR_ENABLED", "false").lower() == "true"
LPR_CLASSES = [c.strip() for c in DEF("LPR_CLASSES", "car,truck,bus").split(",") if c.strip()]

# Inference backend (local GPU, cloud, or hybrid fallback)
INFERENCE_MODE = DEF("INFERENCE_MODE", "local").lower()
INFERENCE_CLOUD_ENDPOINT = DEF("INFERENCE_CLOUD_ENDPOINT", "https://api.basebuddy.io/v1")
INFERENCE_CLOUD_API_KEY = DEF("INFERENCE_CLOUD_API_KEY", "")
INFERENCE_HYBRID_FALLBACK = DEF("INFERENCE_HYBRID_FALLBACK", "true").lower() == "true"
try:
    INFERENCE_CLOUD_TIMEOUT_S = float(DEF("INFERENCE_CLOUD_TIMEOUT_S", "5"))
except (TypeError, ValueError):
    INFERENCE_CLOUD_TIMEOUT_S = 5.0

# Home scenes (pantry / fridge monitoring)
HOME_SCENES_ENABLE = DEF("HOME_SCENES_ENABLE", "true").lower() == "true"

# Web UI authentication (recommended for non-localhost production)
AUTH_ENABLE = DEF("AUTH_ENABLE", "false").lower() == "true"
ADMIN_USERNAME = DEF("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = DEF("ADMIN_PASSWORD", "")
SECRET_KEY = DEF("SECRET_KEY", "")

# Pixel calibration
PX_PER_M = int(DEF("PX_PER_M", "50"))

# Database configuration
DB_PATH = abs_data_path(DEF("DB_PATH", "analytics.db"))

# Media storage configuration (for thumbnails/full-size crops)
_LOCAL_MEDIA_PATH = os.path.join(get_repo_root(), "media")
os.makedirs(_LOCAL_MEDIA_PATH, exist_ok=True)
MEDIA_BASE_DIR = abs_data_path(DEF("MEDIA_BASE_DIR", _LOCAL_MEDIA_PATH))
MEDIA_URL_PREFIX = DEF("MEDIA_URL_PREFIX", "/media")

# Deduplication configuration (to reduce near-duplicate detections/thumbnails)
DEDUP_ENABLE = DEF("DEDUP_ENABLE", "true").lower() == "true"
DEDUP_TIME_WINDOW_S = int(DEF("DEDUP_TIME_WINDOW_S", "10"))  # recent window to compare
DEDUP_CENTER_PX = int(DEF("DEDUP_CENTER_PX", "40"))          # center distance threshold
DEDUP_IOU = float(DEF("DEDUP_IOU", "0.6"))                   # IoU threshold for spatial dupes
DEDUP_PHASH_MAX_DIST = int(DEF("DEDUP_PHASH_MAX_DIST", "8"))  # max pHash Hamming distance to consider duplicate

# Ignore future detections of the same class in the same screen region (false-positive zones)
FALSE_POSITIVE_ZONES_ENABLE = DEF("FALSE_POSITIVE_ZONES_ENABLE", "true").lower() == "true"
FALSE_POSITIVE_ZONE_IOU = float(DEF("FALSE_POSITIVE_ZONE_IOU", "0.35"))  # new box vs saved zone IoU ≥ this → not stored

# Traffic analysis configuration
# -1 disables traffic analysis; otherwise set to 0-3 for the camera index
try:
    _traffic_cam_raw = DEF("TRAFFIC_CAM_ID", "-1")
    TRAFFIC_CAM_ID = int(_traffic_cam_raw) if _traffic_cam_raw not in (None, "", "null") else -1
except ValueError:
    TRAFFIC_CAM_ID = -1

def read_config_export(key: str, default: str = "") -> str:
    """
    Read a single `export KEY=...` value directly from the repo-root config.txt.

    Bypasses os.environ so callers always see the latest on-disk value
    (used by the reload_* helpers below when the UI saves settings).
    """
    try:
        from basebuddy.core.config_persist import parse_export_value
        with open(_config_txt_path(), 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('export ') and '=' in line:
                    k, v = line[7:].split('=', 1)
                    if k.strip() == key:
                        return parse_export_value(v)
    except Exception:
        pass
    return default


def _load_json_export(key: str, default_json: str):
    """Load a JSON value from config.txt, falling back to the environment."""
    raw = read_config_export(key, "")
    if not raw or raw == default_json:
        raw = DEF(key, default_json)
    try:
        return json.loads(raw) if raw else json.loads(default_json)
    except Exception as e:
        logger.error(f"Error parsing {key} from config: {e}")
        return json.loads(default_json)


# Thresholds configuration
def load_class_thresholds() -> Dict[str, Dict[str, float]]:
    """Load per-camera, per-class thresholds from config"""
    return _load_json_export("CLASS_THRESHOLDS", "{}")

def reload_class_thresholds() -> Dict[str, Dict[str, float]]:
    """Reload per-camera, per-class thresholds from config file"""
    return _load_json_export("CLASS_THRESHOLDS", "{}")

CLASS_THRESHOLDS = load_class_thresholds()

# Disabled classes configuration
def load_disabled_classes() -> List[str]:
    """Load list of disabled detection classes"""
    return _load_json_export("DISABLED_CLASSES", "[]")

def reload_disabled_classes() -> List[str]:
    """Reload disabled classes from config file"""
    return _load_json_export("DISABLED_CLASSES", "[]")

DISABLED_CLASSES = load_disabled_classes()

# Ignored detections configuration
def load_ignored_detections() -> Dict[str, List[Dict]]:
    """Load list of ignored detections by camera"""
    return _load_json_export("IGNORED_DETECTIONS", "{}")

def reload_ignored_detections() -> Dict[str, List[Dict]]:
    """Reload ignored detections from config file"""
    return _load_json_export("IGNORED_DETECTIONS", "{}")

IGNORED_DETECTIONS = load_ignored_detections()

# Tracking configuration ({"global": {...}, "cameras": {"<id>": {...}}})
def load_tracking_config() -> Dict[str, Any]:
    """Load tracking configuration overrides from config file"""
    return _load_json_export("TRACKING_CONFIG", "{}")

# Ignored ROIs configuration (per-camera list of rectangles in normalized coords)
def load_ignored_rois() -> Dict[str, List[Dict]]:
    """Load per-camera labeled regions (normalized 0–1 polygon points).

    Stored under IGNORED_ROIS for backward compatibility. Each region:
    {"id","label","shape","points":[[x,y],...],"filter":"include|exclude|none",
     "tag_detections":bool,"analytics":bool,"notify":{"enabled":bool,"classes":[],"cooldown_s":int}}

    Legacy rects {"x1","y1","x2","y2","mode"} are migrated automatically.
    """
    return _load_json_export("IGNORED_ROIS", "{}")

def reload_ignored_rois() -> Dict[str, List[Dict]]:
    """Reload ignored ROIs from config file."""
    global IGNORED_ROIS
    IGNORED_ROIS = _load_json_export("IGNORED_ROIS", "{}")
    return IGNORED_ROIS

IGNORED_ROIS = load_ignored_rois()

# OpenCV configuration
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|max_delay;0")

# Detection availability
try:
    from ultralytics import YOLO
    import supervision as sv
    YOLO_AVAILABLE = True
except ImportError:
    logger.warning("Warning: ultralytics or supervision not available. Install with: pip install ultralytics supervision")
    YOLO_AVAILABLE = False
    YOLO = None
    sv = None

# Ignored Instances (persistent per-camera appearance signatures)
def load_ignored_instances() -> Dict[str, List[Dict]]:
    """Load list of ignored instances by camera.
    Format: {"camera_0": [{"class_name":str, "bbox":[x1,y1,x2,y2], "hist":[...], "added_at":ts}], ...}
    """
    return _load_json_export("IGNORED_INSTANCES", "{}")

def reload_ignored_instances() -> Dict[str, List[Dict]]:
    """Reload ignored instances from config file"""
    return load_ignored_instances()

IGNORED_INSTANCES = load_ignored_instances()
