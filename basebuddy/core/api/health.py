"""Health check API."""
from flask import Blueprint, jsonify

health_api = Blueprint("health_api", __name__)


@health_api.route("/health")
def health():
    """Liveness and basic dependency check (unauthenticated)."""
    status = {"ok": True, "service": "basebuddy"}
    try:
        import basebuddy.modules.state as shared_state

        status["cameras"] = len(shared_state.grabbers or {})
        status["database"] = shared_state.analytics_db is not None
    except Exception as exc:
        status["ok"] = False
        status["error"] = str(exc)
        return jsonify(status), 503

    try:
        from basebuddy.modules.resource_manager import get_resource_manager

        rm = get_resource_manager()
        gpu = rm.monitor.get_gpu_stats()
        if gpu:
            status["gpu"] = {
                "memory_percent": round(gpu.memory_utilization_percent, 1),
                "available": True,
            }
        else:
            status["gpu"] = {"available": False}
    except Exception:
        status["gpu"] = {"available": False}

    try:
        from basebuddy.modules.config import INFERENCE_MODE
        from basebuddy.core.inference import get_inference_router

        router = get_inference_router()
        status["inference"] = {"mode": INFERENCE_MODE, "router": router.mode}
    except Exception:
        status["inference"] = {"mode": "unknown"}

    try:
        from basebuddy.core.services.detection_watchdog import detection_health

        detection = detection_health(shared_state.grabbers)
        status["detection"] = detection
        if detection.get("stale"):
            status["ok"] = False
    except Exception as exc:
        status["detection"] = {"stale": True, "error": str(exc)}
        status["ok"] = False

    # Storage: informational block (does not flip "ok", so liveness checks are unaffected).
    status["storage"] = storage_health_block()

    code = 200 if status["ok"] else 503
    return jsonify(status), code


def storage_health_block() -> dict:
    """ok | warning | critical summary of backup drive + local disk + analytics.db."""
    order = {"ok": 0, "warning": 1, "critical": 2}
    block = {"level": "ok", "reason": "ok"}
    reasons = []
    try:
        import basebuddy.modules.state as shared_state

        bm = getattr(shared_state, "backup_manager", None)
        if bm is not None and hasattr(bm, "storage_health"):
            b = bm.storage_health()
            block["backup"] = b
            if order.get(b.get("level"), 0) > order[block["level"]]:
                block["level"] = b["level"]
            if b.get("level") != "ok":
                reasons.append(b.get("reason", ""))
            block["free_gb"] = b.get("free_gb")
            block["last_success"] = b.get("last_success")
            block["crowded_out"] = b.get("crowded_out", False)
        rs = getattr(shared_state, "retention_service", None)
        if rs is not None:
            st = getattr(rs, "_state", {}) or {}
            adb = st.get("analytics_db") or {}
            if adb.get("warnings"):
                reasons.extend(adb["warnings"])
                if block["level"] == "ok":
                    block["level"] = "warning"
            pressure = st.get("last_disk_pressure") or {}
            for dev in pressure.get("devices", []) or []:
                if dev.get("deleted"):
                    reasons.append(f"local disk pressure at {dev.get('probe')}: freed {dev.get('freed_mb')} MB")
                    if block["level"] == "ok":
                        block["level"] = "warning"
    except Exception as exc:
        block["level"] = "warning"
        reasons.append(f"storage status error: {exc}")
    if reasons:
        block["reason"] = "; ".join(r for r in reasons if r)
    return block
