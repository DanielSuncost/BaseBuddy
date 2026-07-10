"""Model download and system readiness API."""
from flask import Blueprint, jsonify, request

models_api = Blueprint("models_api", __name__)


@models_api.route("/status", methods=["GET"])
def models_status():
    try:
        from basebuddy.modules.model_manager import get_models_status
        return jsonify({"ok": True, "data": get_models_status()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@models_api.route("/download", methods=["POST"])
def models_download():
    """
    Download model weights.

    Body (optional):
      { "scope": "recommended"|"required"|"missing"|"all", "models": ["yolov8n.pt"] }
    """
    try:
        from basebuddy.modules.model_manager import download_models, get_models_status, resolve_download_set

        payload = request.get_json(force=True) if request.data else {}
        scope = (payload.get("scope") or "recommended").lower()
        explicit = payload.get("models")

        names = resolve_download_set(scope=scope, explicit=explicit)
        if not names:
            status = get_models_status()
            return jsonify({
                "ok": True,
                "message": "All selected models are already installed",
                "data": {"downloaded": [], "failed": [], "status": status},
            })

        # Preflight: warn if disk space looks tight
        status = get_models_status()
        need_mb = sum(
            m.get("download_size_mb") or 0
            for m in status["models"]
            if m["id"] in names and not m["installed"]
        )
        disk_gb = status["system"].get("disk_free_gb") or 0
        if disk_gb > 0 and need_mb > disk_gb * 1024 * 0.9:
            return jsonify({
                "ok": False,
                "error": f"Not enough disk space (~{need_mb}MB needed, {disk_gb}GB free)",
            }), 400

        result = download_models(names)
        status = get_models_status()
        ok = len(result["failed"]) == 0
        return jsonify({
            "ok": ok,
            "message": "Download complete" if ok else "Some downloads failed",
            "data": {**result, "status": status},
        }), 200 if ok else 207
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@models_api.route("/export", methods=["POST"])
def models_export():
    """Export .pt to TensorRT or OpenVINO. Body: { model, backend: tensorrt|openvino }"""
    try:
        from basebuddy.modules.model_manager import export_model_backend
        payload = request.get_json(force=True) if request.data else {}
        model = payload.get("model") or "yolov8n.pt"
        backend = (payload.get("backend") or "tensorrt").lower()
        result = export_model_backend(model, backend=backend)
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
