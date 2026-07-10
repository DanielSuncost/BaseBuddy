"""
Graceful shutdown for BaseBuddy (Ctrl+C / SIGTERM).

Stops camera grabbers and background services so the process can exit cleanly
instead of hanging in the Werkzeug/SocketIO server loop.
"""
from __future__ import annotations

import logging
import os
import signal
import threading

logger = logging.getLogger("basebuddy")

_lock = threading.Lock()
_registered = False
_shutdown_count = 0


def shutdown_basebuddy() -> None:
    """Stop cameras, services, and monitors. Safe to call more than once."""
    with _lock:
        logger.info("Shutting down BaseBuddy...")

        try:
            import basebuddy.modules.state as shared_state

            for cam_id, grabber in list(getattr(shared_state, "grabbers", {}).items()):
                try:
                    grabber.stop()
                except Exception as exc:
                    logger.warning("Camera %s stop error: %s", cam_id, exc)
            shared_state.grabbers.clear()

            for name in (
                "backup_manager",
                "archive_service",
                "retention_service",
                "health_monitor",
            ):
                svc = getattr(shared_state, name, None)
                if svc is not None and hasattr(svc, "stop"):
                    try:
                        svc.stop()
                    except Exception as exc:
                        logger.warning("%s stop error: %s", name, exc)
        except Exception as exc:
            logger.warning("State shutdown error: %s", exc)

        try:
            from basebuddy.modules.resource_monitor import get_resource_monitor

            get_resource_monitor().stop()
        except Exception:
            pass

        try:
            from basebuddy.core.utils.system_health import stop_resource_monitor

            stop_resource_monitor()
        except Exception:
            pass

        try:
            from basebuddy.plugins.home_scenes.scheduler import get_scene_scheduler

            get_scene_scheduler().stop()
        except Exception:
            pass

        try:
            import pynvml

            pynvml.nvmlShutdown()
        except Exception:
            pass

        logger.info("Shutdown complete")


def _signal_handler(signum, frame) -> None:  # noqa: ARG001
    global _shutdown_count
    _shutdown_count += 1

    if _shutdown_count >= 2:
        logger.warning("Forced exit")
        os._exit(128 + signum)

    logger.info("Interrupt received — stopping (press Ctrl+C again to force quit)...")
    shutdown_basebuddy()
    raise KeyboardInterrupt


def register_shutdown_handlers() -> None:
    """Install SIGINT/SIGTERM handlers once."""
    global _registered
    if _registered:
        return
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    _registered = True
