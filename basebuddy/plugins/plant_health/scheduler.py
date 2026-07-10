"""Custom schedules for plant capture + analysis."""
from __future__ import annotations

import threading
import time
from datetime import datetime, time as dt_time
from typing import Dict, Optional

from basebuddy.plugins.plant_health.config import get_monitor, list_monitors
from basebuddy.plugins.plant_health.service import run_monitor_cycle
import logging

logger = logging.getLogger(__name__)


class PlantScheduler:
    def __init__(self, tick_s: float = 45.0):
        self.tick_s = tick_s
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_fired: Dict[str, float] = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="plant-scheduler")
        self._thread.start()
        logger.info("Plant health scheduler started")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.run_due_checks()
            except Exception as exc:
                logger.error(f"Plant scheduler error: {exc}")
            time.sleep(self.tick_s)

    def run_due_checks(self) -> None:
        now = datetime.now()
        for monitor in list_monitors():
            if not monitor.get("enabled", True):
                continue
            mid = monitor.get("id")
            schedule = monitor.get("schedule") or {}
            if schedule.get("enabled") is False:
                continue
            last = self._last_fired.get(mid)
            if last is None:
                last = _last_sample_ts(mid)
            if is_schedule_due(monitor, last, now):
                self._last_fired[mid] = now.timestamp()
                run_monitor_cycle(mid, trigger="on_schedule")

    def run_now(self, monitor_id: str) -> dict:
        self._last_fired[monitor_id] = time.time()
        return run_monitor_cycle(monitor_id, trigger="manual")


def _last_sample_ts(monitor_id: str) -> Optional[float]:
    from basebuddy.plugins.plant_health.db import latest_color_sample_ts
    return latest_color_sample_ts(monitor_id)


def is_schedule_due(monitor: dict, last_run: Optional[float], now: datetime) -> bool:
    schedule = monitor.get("schedule") or {}
    mode = schedule.get("mode") or "interval"
    if mode == "interval":
        interval = int(schedule.get("interval_s") or monitor.get("check_interval_s") or 3600)
        if last_run is None:
            return True
        return (now.timestamp() - last_run) >= interval

    if mode == "times":
        times = schedule.get("times") or []
        if not times:
            return False
        days = schedule.get("days")
        if days is not None and now.weekday() not in days:
            return False
        for tstr in times:
            try:
                parts = tstr.strip().split(":")
                h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                continue
            slot = datetime.combine(now.date(), dt_time(h, m))
            slot_ts = slot.timestamp()
            if slot_ts <= now.timestamp():
                if last_run is None or last_run < slot_ts:
                    return True
        return False

    return False


_scheduler: Optional[PlantScheduler] = None


def get_plant_scheduler() -> PlantScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = PlantScheduler()
    return _scheduler
