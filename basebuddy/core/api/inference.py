"""Inference backend status and configuration API."""
from flask import Blueprint, jsonify, request

inference_api = Blueprint("inference_api", __name__)


@inference_api.route("/status")
def inference_status():
    from basebuddy.modules.config import (
        INFERENCE_CLOUD_API_KEY,
        INFERENCE_CLOUD_ENDPOINT,
        INFERENCE_HYBRID_FALLBACK,
        INFERENCE_MODE,
    )

    cloud_configured = bool(INFERENCE_CLOUD_API_KEY and INFERENCE_CLOUD_ENDPOINT)
    usage = None
    if cloud_configured and INFERENCE_MODE in ("cloud", "hybrid"):
        try:
            from basebuddy.core.inference.cloud.client import CloudClient

            client = CloudClient(INFERENCE_CLOUD_ENDPOINT, INFERENCE_CLOUD_API_KEY)
            u = client.get_usage()
            usage = {"frames_consumed": u.frames_consumed, "quota_remaining": u.quota_remaining}
        except Exception as exc:
            usage = {"error": str(exc)}

    return jsonify({
        "ok": True,
        "data": {
            "mode": INFERENCE_MODE,
            "hybrid_fallback": INFERENCE_HYBRID_FALLBACK,
            "cloud_configured": cloud_configured,
            "cloud_endpoint": INFERENCE_CLOUD_ENDPOINT if cloud_configured else None,
            "usage": usage,
        },
    })
