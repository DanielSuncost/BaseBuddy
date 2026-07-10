"""REST API for pantry / fridge / shelf scenes."""
from flask import Blueprint, jsonify, request

from basebuddy.plugins.home_scenes.config import create_scene, delete_scene, get_scene, list_scenes, update_scene
from basebuddy.plugins.home_scenes.db import list_scene_events, list_scene_states
from basebuddy.plugins.home_scenes.scheduler import get_scene_scheduler

scenes_api = Blueprint("scenes_api", __name__)


def _scene_with_states(scene: dict) -> dict:
    states = {s["slot_id"]: s for s in list_scene_states(scene["id"])}
    slots = []
    for slot in scene.get("slots", []):
        sid = slot.get("id")
        st = states.get(sid, {})
        slots.append({**slot, **{
            "state": st.get("state", "unknown"),
            "confidence": st.get("confidence"),
            "last_checked_at": st.get("last_checked_at"),
            "consecutive_empty": st.get("consecutive_empty", 0),
        }})
    return {**scene, "slots": slots}


@scenes_api.route("/camera/<int:camera_id>/preview", methods=["GET"])
def api_camera_preview(camera_id):
    """Latest frame from a camera as JPEG (for ROI editor and scene setup)."""
    import cv2
    from flask import Response
    import basebuddy.modules.state as shared_state

    max_w = request.args.get("w", type=int)

    grabber = shared_state.grabbers.get(camera_id)
    if grabber is None:
        return jsonify({"ok": False, "error": "camera not active"}), 404
    frame, _ts = grabber.get_latest_frame()
    if frame is None:
        return jsonify({"ok": False, "error": "no frame available"}), 503
    if max_w and max_w > 0 and frame.shape[1] > max_w:
        scale = max_w / float(frame.shape[1])
        frame = cv2.resize(
            frame,
            (max_w, max(1, int(frame.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        return jsonify({"ok": False, "error": "encode failed"}), 500
    return Response(buf.tobytes(), mimetype="image/jpeg")


@scenes_api.route("", methods=["GET"])
def api_list_scenes():
    scenes = [_scene_with_states(s) for s in list_scenes()]
    return jsonify({"ok": True, "data": {"scenes": scenes}})


@scenes_api.route("", methods=["POST"])
def api_create_scene():
    payload = request.get_json(force=True) or {}
    scene = create_scene(payload)
    return jsonify({"ok": True, "data": _scene_with_states(scene)})


@scenes_api.route("/<scene_id>", methods=["GET"])
def api_get_scene(scene_id):
    scene = get_scene(scene_id)
    if not scene:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "data": _scene_with_states(scene)})


@scenes_api.route("/<scene_id>", methods=["PUT"])
def api_update_scene(scene_id):
    payload = request.get_json(force=True) or {}
    scene = update_scene(scene_id, payload)
    if not scene:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "data": _scene_with_states(scene)})


@scenes_api.route("/<scene_id>", methods=["DELETE"])
def api_delete_scene(scene_id):
    if not delete_scene(scene_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True})


@scenes_api.route("/<scene_id>/check", methods=["POST"])
def api_check_scene(scene_id):
    result = get_scene_scheduler().check_scene(scene_id)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@scenes_api.route("/<scene_id>/slots/<slot_id>/baseline", methods=["POST"])
def api_set_baseline(scene_id, slot_id):
    path = get_scene_scheduler().capture_baseline(scene_id, slot_id)
    if not path:
        return jsonify({"ok": False, "error": "could not capture baseline"}), 400
    return jsonify({"ok": True, "data": {"baseline_image": path}})


@scenes_api.route("/<scene_id>/events", methods=["GET"])
def api_scene_events(scene_id):
    limit = request.args.get("limit", 50, type=int)
    events = list_scene_events(scene_id, limit=limit)
    return jsonify({"ok": True, "data": {"events": events}})
