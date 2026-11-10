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
    for prefix in ("recordings/", "detections/", "stills/", "timelapse_output/", "media/"):
        idx = p.find(prefix)
        if idx >= 0:
            return "/" + p[idx:]
    if p.startswith("/"):
        return p
    return "/" + p.lstrip("/")


def _enrich_session(row: dict) -> dict:
    row = dict(row)
    row["snapshot_url"] = _public_url(row.get("snapshot_path") or "")
    row["clip_url"] = _public_url(row.get("clip_path") or "")
    return row


@events_api.route("/api/events/sessions", methods=["GET"])
def list_sessions():
    try:
        cam = request.args.get("camera_id")
        camera_id = int(cam) if cam not in (None, "") else None
        class_name = request.args.get("class") or request.args.get("class_name")
        state = request.args.get("state")
        # Prefer per_page; fall back to limit. Default 50, hard cap 500.
        if request.args.get("per_page") is not None:
            limit = min(500, max(1, int(request.args.get("per_page"))))
        else:
            limit = min(500, max(1, int(request.args.get("limit", 50))))
        if request.args.get("page") is not None:
            page = max(1, int(request.args.get("page")))
            offset = (page - 1) * limit
        else:
            offset = max(0, int(request.args.get("offset", 0)))
            page = (offset // limit) + 1
        hours = request.args.get("hours")
        since = None
        if hours:
            import time
            since = time.time() - float(hours) * 3600
        filt = dict(
            camera_id=camera_id,
            class_name=class_name,
            state=state,
            since=since,
        )
        total = _db().count_event_sessions(**filt)
        rows = _db().list_event_sessions(limit=limit, offset=offset, **filt)
        return jsonify({
            "ok": True,
            "sessions": [_enrich_session(r) for r in rows],
            "count": len(rows),
            "total": total,
            "limit": limit,
            "offset": offset,
            "page": page,
            "per_page": limit,
            "has_more": (offset + len(rows)) < total,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@events_api.route("/api/events/sessions/<session_id>", methods=["GET"])
def get_session(session_id):
    try:
        row = _db().get_event_session(session_id)
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "session": _enrich_session(row)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@events_api.route("/api/events/sessions/<session_id>/nearby", methods=["GET"])
def nearby_frames(session_id):
    """Nearby detection frames for GIF / animation (track-first, else camera+class)."""
    try:
        window_s = float(request.args.get("window_s", 10))
        same_class = request.args.get("same_class", "1") not in ("0", "false", "False")
        limit = int(request.args.get("limit", 80))
        result = _db().get_nearby_detections_for_session(
            session_id, window_s=window_s, same_class=same_class, limit=limit,
        )
        if not result.get("session"):
            return jsonify({"ok": False, "error": "not found"}), 404
        dets = []
        for d in result.get("detections") or []:
            d = dict(d)
            d["image_url"] = _public_url(d.get("full_image_path") or d.get("thumbnail_path") or "")
            d["thumb_url"] = _public_url(d.get("thumbnail_path") or d.get("full_image_path") or "")
            dets.append(d)
        return jsonify({
            "ok": True,
            "session": _enrich_session(result["session"]),
            "detections": dets,
            "count": len(dets),
            "mode": result.get("mode"),
            "window_s": result.get("window_s"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@events_api.route("/api/events/sessions/<session_id>/adjacent-clips", methods=["GET"])
def adjacent_clips(session_id):
    """Clips that overlap or sit within gap_s of this session (stitch candidates)."""
    try:
        gap_s = float(request.args.get("gap_s", 15))
        prefer_same_class = request.args.get("same_class", "1") not in ("0", "false", "False")
        cluster = _db().find_adjacent_clip_sessions(
            session_id, gap_s=gap_s, prefer_same_class=prefer_same_class,
        )
        enriched = [_enrich_session(s) for s in cluster]
        stitchable = [s for s in enriched if s.get("clip_url") or s.get("clip_path")]
        return jsonify({
            "ok": True,
            "sessions": enriched,
            "stitchable_count": len(stitchable),
            "can_stitch": len(stitchable) >= 2,
            "gap_s": gap_s,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@events_api.route("/api/events/stitch", methods=["POST"])
def stitch_clips():
    """Stitch selected event clips with overlapping time ranges deduped."""
    try:
        data = request.get_json() or {}
        session_ids = data.get("session_ids") or []
        if not session_ids and data.get("session_id"):
            # Expand from seed via adjacent cluster
            session_ids = [data["session_id"]]
            gap_s = float(data.get("gap_s", 15))
            cluster = _db().find_adjacent_clip_sessions(data["session_id"], gap_s=gap_s)
            session_ids = [s["id"] for s in cluster if s.get("clip_path")]

        if len(session_ids) < 2:
            return jsonify({
                "ok": False,
                "error": "Need at least 2 session clips to stitch",
            }), 400

        from basebuddy.modules.config import EVENT_CLIP_PRE_S, EVENT_CLIP_POST_S
        from basebuddy.core.services.event_clip_stitch import stitch_clips as do_stitch

        sessions = []
        for sid in session_ids:
            row = _db().get_event_session(sid)
            if row and row.get("clip_path"):
                # Attach estimated clip window for planner
                pre = float(EVENT_CLIP_PRE_S)
                post = float(EVENT_CLIP_POST_S)
                t0 = float(row["started_at"] or 0) - pre
                t1 = float(row.get("ended_at") or row.get("updated_at") or row["started_at"] or 0) + post
                row["clip_t0"] = t0
                row["clip_t1"] = t1
                sessions.append(row)

        if len(sessions) < 2:
            return jsonify({"ok": False, "error": "Fewer than 2 resolvable clips"}), 400

        sessions.sort(key=lambda s: float(s["started_at"] or 0))
        camera_id = int(sessions[0]["camera_id"])
        result = do_stitch(
            sessions,
            pre_s=float(EVENT_CLIP_PRE_S),
            post_s=float(EVENT_CLIP_POST_S),
            camera_id=camera_id,
        )
        if not result:
            return jsonify({"ok": False, "error": "Stitch failed (ffmpeg)"}), 500

        return jsonify({
            "ok": True,
            "url": result["url"],
            "path": result.get("relative_path"),
            "plan": result.get("plan"),
            "clip_count": result.get("clip_count"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
