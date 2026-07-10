"""Scheduler for automated plant blog / social posts."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Dict, Optional

from basebuddy.plugins.plant_health.blogger import publish_channel
from basebuddy.plugins.plant_health.blogger_config import list_channels
from basebuddy.plugins.plant_health.db import latest_blogger_post_ts
from basebuddy.plugins.plant_health.scheduler import is_schedule_due
import logging

logger = logging.getLogger(__name__)


class PlantBloggerScheduler:
    def __init__(self, tick_s: float = 60.0):
        self.tick_s = tick_s
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_fired: Dict[str, float] = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="plant-blogger")
        self._thread.start()
        logger.info("Plant blogger scheduler started")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.run_due_posts()
            except Exception as exc:
                logger.error(f"Plant blogger scheduler error: {exc}")
            time.sleep(self.tick_s)

    def run_due_posts(self) -> None:
        now = datetime.now()
        for channel in list_channels():
            if not channel.get("enabled", True):
                continue
            schedule = channel.get("schedule") or {}
            if schedule.get("enabled") is False:
                continue
            cid = channel.get("id")
            if not cid:
                continue
            last = self._last_fired.get(cid)
            if last is None:
                last = latest_blogger_post_ts(cid)
            pseudo_monitor = {"schedule": schedule, "check_interval_s": schedule.get("interval_s") or 86400}
            if is_schedule_due(pseudo_monitor, last, now):
                self._last_fired[cid] = now.timestamp()
                result = publish_channel(cid, trigger="scheduled")
                if not result.get("ok"):
                    logger.error(f"Plant blogger {cid}: {result.get('error')}")

    def run_now(self, channel_id: str) -> dict:
        self._last_fired[channel_id] = time.time()
        return publish_channel(channel_id, trigger="manual")


_scheduler: Optional[PlantBloggerScheduler] = None


def get_blogger_scheduler() -> PlantBloggerScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = PlantBloggerScheduler()
    return _scheduler
