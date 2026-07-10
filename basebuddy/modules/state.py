from __future__ import annotations

from typing import Dict, Any

from basebuddy.modules.database import AnalyticsDB

# Shared application state
grabbers: Dict[int, object] = {}
detectors: Dict[int, object] = {}
analytics_db = AnalyticsDB()
# Set by main.py (core.services.backup_service.BackupManager); must start as None
# so main can install the configured instance.
backup_manager: Any = None
health_monitor = None
archive_service = None
retention_service = None

# Recording status tracking
recording_status: Dict[int, dict] = {}



