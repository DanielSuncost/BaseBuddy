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

    code = 200 if status["ok"] else 503
    return jsonify(status), code
