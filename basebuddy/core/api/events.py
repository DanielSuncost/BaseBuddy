"""Events timeline API — track-based event sessions."""
from flask import Blueprint, jsonify, request

events_api = Blueprint("events_api", __name__)


def _db():
    import basebuddy.modules.state as st
    return st.analytics_db


def _public_url(path: str) -> str:
    if not path:
        return ""
    p = path.replace("\\", "/")
    if p.startswith("http"):
        return p
    for prefix in ("recordings/", "detections/", "stills/", "timelapse_output/"):
        idx = p.find(prefix)
        if idx >= 0:
            return "/" + p[idx:]
    return "/" + p.lstrip("/")


@events_api.route("/api/events/sessions", methods=["GET"])
def list_sessions():
    try:
        cam = request.args.get("camera_id")
        camera_id = int(cam) if cam not in (None, "") else None
        class_name = request.args.get("class") or request.args.get("class_name")
        state = request.args.get("state")
        limit = min(500, int(request.args.get("limit", 100)))
        offset = int(request.args.get("offset", 0))
        hours = request.args.get("hours")
        since = None
        if hours:
            import time
            since = time.time() - float(hours) * 3600
        rows = _db().list_event_sessions(
            camera_id=camera_id,
            class_name=class_name,
            state=state,
            limit=limit,
            offset=offset,
            since=since,
        )
        for r in rows:
            r["snapshot_url"] = _public_url(r.get("snapshot_path") or "")
            r["clip_url"] = _public_url(r.get("clip_path") or "")
        return jsonify({"ok": True, "sessions": rows, "count": len(rows)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@events_api.route("/api/events/sessions/<session_id>", methods=["GET"])
def get_session(session_id):
    try:
        with _db()._connect() as conn:
            cur = conn.execute(
                "SELECT id, camera_id, class_name, track_id, started_at, updated_at, ended_at, "
                "state, max_confidence, snapshot_path, clip_path, region_labels, plate_text "
                "FROM event_sessions WHERE id = ?",
                (session_id,),
            )
            r = cur.fetchone()
        if not r:
            return jsonify({"ok": False, "error": "not found"}), 404
        row = {
            "id": r[0], "camera_id": r[1], "class_name": r[2], "track_id": r[3],
            "started_at": r[4], "updated_at": r[5], "ended_at": r[6], "state": r[7],
            "max_confidence": r[8], "snapshot_path": r[9], "clip_path": r[10],
            "region_labels": r[11], "plate_text": r[12],
        }
        row["snapshot_url"] = _public_url(row.get("snapshot_path") or "")
        row["clip_url"] = _public_url(row.get("clip_path") or "")
        return jsonify({"ok": True, "session": row})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
