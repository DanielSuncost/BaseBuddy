"""Plant health REST API."""
from flask import Blueprint, jsonify, request

from basebuddy.core.config_persist import upsert_config_exports
from basebuddy.core.paths import get_repo_root
from basebuddy.plugins.plant_health.actions import ACTION_TYPES, run_actions
from basebuddy.plugins.plant_health.config import (
    create_monitor,
    delete_monitor,
    get_monitor,
    list_monitors,
    update_monitor,
)
from basebuddy.plugins.plant_health.db import list_analyses, list_blogger_posts, list_color_timeline
from basebuddy.plugins.plant_health.prompts import PREMIUM_PROMPT_NOTE
from basebuddy.plugins.plant_health.scheduler import get_plant_scheduler
from basebuddy.plugins.plant_health.blogger import DESTINATION_TYPES, preview_channel
from basebuddy.plugins.plant_health.blogger_config import (
    create_channel,
    delete_channel,
    list_channels,
    update_channel,
)
from basebuddy.plugins.plant_health.blogger_scheduler import get_blogger_scheduler
from basebuddy.plugins.plant_health.service import analyze_monitor, run_monitor_cycle
from basebuddy.plugins.plant_health.segmentation import render_preview_with_points, save_segmentation_pattern

plant_health_api = Blueprint("plant_health_api", __name__)

_VISION_KEYS = ("PLANT_VISION_API_URL", "PLANT_VISION_API_KEY", "PLANT_VISION_MODEL")


@plant_health_api.route("/settings", methods=["GET"])
def get_settings():
    from basebuddy.modules.config import env_live
    from basebuddy.plugins.plant_health.analyzer import OSSPlantAnalyzer

    a = OSSPlantAnalyzer()
    return jsonify({
        "ok": True,
        "configured": a.is_configured(),
        "api_url": env_live("PLANT_VISION_API_URL", "") or "",
        "model": env_live("PLANT_VISION_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
        "api_key_set": bool(env_live("PLANT_VISION_API_KEY", "")),
        "premium_note": PREMIUM_PROMPT_NOTE,
        "action_types": list(ACTION_TYPES),
    })


@plant_health_api.route("/settings", methods=["POST"])
def save_settings():
    try:
        body = request.get_json(silent=True) or {}
        updates = {}
        for k in _VISION_KEYS:
            if k in body:
                updates[k] = str(body[k] or "").strip()
        if updates:
            upsert_config_exports(get_repo_root(), updates)
            from basebuddy.modules.config import load_config_file
            load_config_file()
        return get_settings()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@plant_health_api.route("/monitors", methods=["GET"])
def api_list_monitors():
    monitors = list_monitors()
    enriched = []
    for m in monitors:
        last = list_analyses(m["id"], limit=1)
        timeline = list_color_timeline(m["id"], limit=500)
        enriched.append({
            **m,
            "last_analysis": last[0] if last else None,
            "last_color_sample": timeline[-1] if timeline else None,
            "sample_count": len(timeline),
        })
    return jsonify({"ok": True, "monitors": enriched})


@plant_health_api.route("/monitors", methods=["POST"])
def api_create_monitor():
    body = request.get_json(silent=True) or {}
    m = create_monitor(body)
    return jsonify({"ok": True, "monitor": m})


@plant_health_api.route("/monitors/<monitor_id>", methods=["PUT"])
def api_update_monitor(monitor_id):
    body = request.get_json(silent=True) or {}
    m = update_monitor(monitor_id, body)
    if not m:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "monitor": m})


@plant_health_api.route("/monitors/<monitor_id>", methods=["DELETE"])
def api_delete_monitor(monitor_id):
    ok = delete_monitor(monitor_id)
    return jsonify({"ok": ok})


@plant_health_api.route("/monitors/<monitor_id>/analyze", methods=["POST"])
def api_analyze(monitor_id):
    try:
        result = get_plant_scheduler().run_now(monitor_id)
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@plant_health_api.route("/blogger/channels", methods=["GET"])
def api_blogger_list():
    from basebuddy.plugins.plant_health.config import get_monitor

    channels = []
    for ch in list_channels():
        mon = get_monitor(ch.get("monitor_id") or "")
        last_posts = list_blogger_posts(ch.get("id"), limit=1)
        channels.append({
            **ch,
            "monitor_name": mon.get("name") if mon else None,
            "last_post": last_posts[0] if last_posts else None,
        })
    return jsonify({
        "ok": True,
        "channels": channels,
        "destination_types": list(DESTINATION_TYPES),
    })


@plant_health_api.route("/blogger/channels", methods=["POST"])
def api_blogger_create():
    body = request.get_json(silent=True) or {}
    ch = create_channel(body)
    return jsonify({"ok": True, "channel": ch})


@plant_health_api.route("/blogger/channels/<channel_id>", methods=["PUT"])
def api_blogger_update(channel_id):
    body = request.get_json(silent=True) or {}
    ch = update_channel(channel_id, body)
    if not ch:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "channel": ch})


@plant_health_api.route("/blogger/channels/<channel_id>", methods=["DELETE"])
def api_blogger_delete(channel_id):
    ok = delete_channel(channel_id)
    return jsonify({"ok": ok})


@plant_health_api.route("/blogger/channels/<channel_id>/preview", methods=["POST"])
def api_blogger_preview(channel_id):
    try:
        result = preview_channel(channel_id)
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@plant_health_api.route("/blogger/channels/<channel_id>/publish", methods=["POST"])
def api_blogger_publish(channel_id):
    try:
        result = get_blogger_scheduler().run_now(channel_id)
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@plant_health_api.route("/blogger/history", methods=["GET"])
def api_blogger_history():
    channel_id = request.args.get("channel_id")
    limit = min(50, int(request.args.get("limit", 20)))
    return jsonify({"ok": True, "posts": list_blogger_posts(channel_id or None, limit=limit)})


@plant_health_api.route("/monitors/<monitor_id>/history", methods=["GET"])
def api_history(monitor_id):
    limit = min(50, int(request.args.get("limit", 20)))
    return jsonify({"ok": True, "history": list_analyses(monitor_id, limit=limit)})


@plant_health_api.route("/monitors/<monitor_id>/color-timeline", methods=["GET"])
def api_color_timeline(monitor_id):
    limit = min(500, int(request.args.get("limit", 120)))
    samples = list_color_timeline(monitor_id, limit=limit)
    return jsonify({"ok": True, "samples": samples})


@plant_health_api.route("/monitors/<monitor_id>/actions/test", methods=["POST"])
def api_test_action(monitor_id):
    monitor = get_monitor(monitor_id)
    if not monitor:
        return jsonify({"ok": False, "error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action:
        monitor = {**monitor, "actions": [action]}
    results = run_actions(monitor, body.get("trigger") or "manual", {"test": True})
    return jsonify({"ok": True, "results": results})


@plant_health_api.route("/cameras", methods=["GET"])
def api_cameras():
    cams = []
    try:
        import basebuddy.modules.state as st
        with st.analytics_db._connect() as conn:
            cur = conn.execute("SELECT id, name FROM cameras WHERE enabled = 1 ORDER BY id")
            for row in cur.fetchall():
                cams.append({"id": row[0], "name": row[1] or f"Camera {row[0] + 1}"})
    except Exception:
        pass
    if not cams:
        import basebuddy.modules.state as st
        for cid in sorted((st.grabbers or {}).keys()):
            cams.append({"id": cid, "name": f"Camera {cid + 1}"})
    return jsonify({"ok": True, "cameras": cams})


@plant_health_api.route("/segment/preview", methods=["POST"])
def api_segment_preview():
    """Live SAM mask preview from click points."""
    from flask import Response

    try:
        body = request.get_json(silent=True) or {}
        camera_id = int(body.get("camera_id", 0))
        points = body.get("points") or []
        labels = body.get("labels") or []
        data, err = render_preview_with_points(camera_id, points, labels)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        return Response(data, mimetype="image/jpeg")
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@plant_health_api.route("/segment/save", methods=["POST"])
def api_segment_save():
    try:
        body = request.get_json(silent=True) or {}
        result = save_segmentation_pattern(
            body.get("monitor_id") or "",
            int(body.get("camera_id", 0)),
            body.get("points") or [],
            body.get("labels") or [],
        )
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
