"""Plant health REST API."""
import os

from flask import Blueprint, after_this_request, jsonify, request, Response, send_file

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
from basebuddy.plugins.plant_health.db import (
    compute_color_trends,
    count_color_samples,
    latest_color_sample,
    list_analyses,
    list_blogger_posts,
    list_color_datasets,
    list_color_timeline,
    list_watering_events,
    save_watering_event,
)
from basebuddy.plugins.plant_health.plot_stats import daily_metric_deltas
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
from basebuddy.plugins.plant_health.segmentation import (
    capture_frame_bgr,
    render_preview_with_points,
    save_segmentation_pattern,
)

plant_health_api = Blueprint("plant_health_api", __name__)

_VISION_KEYS = ("PLANT_VISION_API_URL", "PLANT_VISION_API_KEY", "PLANT_VISION_MODEL")


def _still_url(path):
    if not path:
        return None
    text = str(path).replace("\\", "/")
    if text.startswith("http") or text.startswith("/"):
        return text
    return "/" + text


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


def _camera_name_map() -> dict:
    try:
        from basebuddy.modules.camera_profiles import get_profile_manager

        manager = get_profile_manager()
        names = {}
        for monitor in list_monitors():
            cid = int(monitor.get("camera_id") or 0)
            if cid in names:
                continue
            profile = manager.get_profile(cid)
            names[cid] = str(getattr(profile, "name", "") or "").strip()
        return names
    except Exception:
        return {}


def _enrich_monitor(m: dict, camera_names: dict = None) -> dict:
    import time as _time

    last = list_analyses(m["id"], limit=1)
    color = latest_color_sample(m["id"])
    since = _time.time() - 7 * 86400
    trend_samples = list_color_timeline(m["id"], limit=120, since=since)
    names = camera_names if camera_names is not None else _camera_name_map()
    cid = int(m.get("camera_id") or 0)
    return {
        **m,
        "camera_name": names.get(cid) or "",
        "last_analysis": last[0] if last else None,
        "last_color_sample": color,
        "sample_count": count_color_samples(m["id"]),
        "trends": compute_color_trends(trend_samples),
    }


def _cohort_monitors(monitor_id: str):
    from basebuddy.plugins.plant_health.subjects import subject_key

    me = get_monitor(monitor_id)
    if not me:
        return None
    names = _camera_name_map()
    key = subject_key(me)
    if key.startswith("solo:"):
        return [_enrich_monitor(me, names)]
    return [_enrich_monitor(m, names) for m in list_monitors() if subject_key(m) == key]


@plant_health_api.route("/monitors", methods=["GET"])
def api_list_monitors():
    names = _camera_name_map()
    enriched = [_enrich_monitor(m, names) for m in list_monitors()]
    return jsonify({"ok": True, "monitors": enriched})


@plant_health_api.route("/monitors/<monitor_id>/names", methods=["PUT"])
def api_rename_monitor(monitor_id):
    from basebuddy.plugins.plant_health.subjects import rename_updates

    monitor = get_monitor(monitor_id)
    if not monitor:
        return jsonify({"ok": False, "error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    members = _cohort_monitors(monitor_id) or [monitor]
    try:
        planned = rename_updates(
            monitor,
            members,
            plant_name=body.get("plant_name") if "plant_name" in body else None,
            angle_name=body.get("angle_name") if "angle_name" in body else None,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    for row in planned:
        mid = row.pop("id")
        update_monitor(mid, row)
    if "camera_name" in body:
        try:
            from basebuddy.modules.camera_profiles import get_profile_manager

            cid = int(monitor.get("camera_id") or 0)
            manager = get_profile_manager()
            camera_name = str(body.get("camera_name") or "").strip()
            with manager._connect() as conn:
                manager._ensure_schema(conn.cursor())
                found = conn.execute(
                    "SELECT 1 FROM camera_profiles WHERE camera_id = ?",
                    (cid,),
                ).fetchone()
                if found:
                    conn.execute(
                        "UPDATE camera_profiles SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE camera_id = ?",
                        (camera_name, cid),
                    )
                    conn.commit()
                else:
                    profile = manager.get_profile(cid)
                    profile.name = camera_name
                    manager.save_profile(profile)
        except Exception as exc:
            return jsonify({"ok": False, "error": "Could not rename camera: %s" % exc}), 500
    return jsonify({"ok": True, "monitor": get_monitor(monitor_id)})


@plant_health_api.route("/monitors", methods=["POST"])
def api_create_monitor():
    body = request.get_json(silent=True) or {}
    m = create_monitor(body)
    return jsonify({"ok": True, "monitor": m})


@plant_health_api.route("/monitors/<monitor_id>", methods=["PUT"])
def api_update_monitor(monitor_id):
    body = request.get_json(silent=True) or {}
    if "frame_crop" in body:
        from basebuddy.plugins.plant_health.frame_crop import normalize_crop

        body = {**body, "frame_crop": normalize_crop(body.get("frame_crop"))}
    m = update_monitor(monitor_id, body)
    if not m:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "monitor": m})


@plant_health_api.route("/monitors/<monitor_id>", methods=["DELETE"])
def api_delete_monitor(monitor_id):
    ok = delete_monitor(monitor_id)
    return jsonify({"ok": ok})


def _playback_frames(monitor_id: str):
    from basebuddy.plugins.plant_health.sam_series import get_active_series, list_growth_samples

    frames = []
    series = get_active_series(monitor_id)
    if series:
        for sample in list_growth_samples(series["id"], limit=500):
            if sample.get("ok") is False:
                continue
            path = sample.get("raw_path") or sample.get("exemplar_path") or sample.get("mask_path")
            if not path or sample.get("sampled_at") is None:
                continue
            frames.append({"sampled_at": float(sample["sampled_at"]), "image_path": path})
    if frames:
        return frames
    from basebuddy.plugins.plant_health.db import list_color_source_stills

    stills = list_color_source_stills(monitor_id)
    return [
        {"sampled_at": float(row["sampled_at"]), "image_path": row["image_path"]}
        for row in stills[-400:]
        if row.get("sampled_at") and row.get("image_path")
    ]


def _angle_jpeg(monitor: dict):
    from basebuddy.plugins.plant_health.reanalyze import resolve_image_path

    path = ((monitor or {}).get("last_color_sample") or {}).get("image_path")
    resolved = resolve_image_path(path) if path else None
    if not resolved:
        return None
    try:
        with open(resolved, "rb") as handle:
            return handle.read()
    except OSError:
        return None


@plant_health_api.route("/monitors/<monitor_id>/cohort", methods=["GET"])
def api_cohort(monitor_id):
    from basebuddy.plugins.plant_health.subjects import build_agent_brief

    members = _cohort_monitors(monitor_id)
    if members is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    brief = build_agent_brief(members)
    return jsonify({"ok": True, "brief": brief, "monitors": [
        {"id": m["id"], "name": m.get("name"), "camera_id": m.get("camera_id"), "subject_id": m.get("subject_id") or ""}
        for m in members
    ]})


@plant_health_api.route("/monitors/<monitor_id>/angles", methods=["POST"])
def api_link_angles(monitor_id):
    from basebuddy.plugins.plant_health.subjects import link_angles

    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    ids = list(body.get("monitor_ids") or [])
    if monitor_id not in ids:
        ids.append(monitor_id)
    result = link_angles(ids, name=body.get("name"))
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@plant_health_api.route("/monitors/<monitor_id>/angles", methods=["DELETE"])
def api_unlink_angle(monitor_id):
    from basebuddy.plugins.plant_health.subjects import unlink_angle

    result = unlink_angle(monitor_id)
    code = 200 if result.get("ok") else 404
    return jsonify(result), code


@plant_health_api.route("/monitors/<monitor_id>/sync-playback", methods=["GET"])
def api_sync_playback(monitor_id):
    from basebuddy.plugins.plant_health.subjects import align_playback

    members = _cohort_monitors(monitor_id)
    if members is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    angles = []
    for member in members:
        angles.append({
            "monitor_id": member["id"],
            "name": member.get("name"),
            "camera_id": member.get("camera_id"),
            "frames": _playback_frames(member["id"]),
        })
    aligned = align_playback(angles)
    return jsonify({"ok": True, **aligned})


@plant_health_api.route("/monitors/<monitor_id>/agent-brief", methods=["GET"])
def api_agent_brief(monitor_id):
    from basebuddy.plugins.plant_health.subjects import build_agent_brief

    members = _cohort_monitors(monitor_id)
    if members is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "brief": build_agent_brief(members)})


@plant_health_api.route("/monitors/<monitor_id>/judge", methods=["POST"])
def api_judge_plant(monitor_id):
    from basebuddy.core.premium_hooks import get_plant_health_analyzer
    from basebuddy.plugins.plant_health.subjects import build_agent_brief

    members = _cohort_monitors(monitor_id)
    if members is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    brief = build_agent_brief(members)
    images = []
    for member in members:
        raw = _angle_jpeg(member)
        if raw:
            label = "%s (cam %s)" % (member.get("name") or "Angle", int(member.get("camera_id") or 0) + 1)
            images.append((label, raw))
    analyzer = get_plant_health_analyzer()
    if not hasattr(analyzer, "analyze_brief"):
        return jsonify({"ok": False, "brief": brief, "error": "Vision analyzer cannot read a multi-angle brief"}), 400
    result = analyzer.analyze_brief(brief, images)
    code = 200 if result.get("ok") else 400
    return jsonify({"ok": bool(result.get("ok")), "brief": brief, "result": result, "image_count": len(images)}), code


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
    import time as _time

    limit = min(2000, int(request.args.get("limit", 400)))
    since = None
    if request.args.get("since"):
        try:
            since = float(request.args.get("since"))
        except (TypeError, ValueError):
            since = None
    elif request.args.get("hours"):
        try:
            hours = float(request.args.get("hours"))
            if hours > 0:
                since = _time.time() - hours * 3600.0
        except (TypeError, ValueError):
            since = None
    series_id = None
    if request.args.get("series_id") not in (None, "", "original"):
        try:
            series_id = int(request.args.get("series_id"))
        except (TypeError, ValueError):
            series_id = None
    samples = list_color_timeline(monitor_id, limit=limit, since=since, series_id=series_id)
    watering = list_watering_events(monitor_id, since=since, limit=100)
    return jsonify({
        "ok": True,
        "samples": samples,
        "trends": compute_color_trends(samples),
        "watering_events": watering,
        "daily_deltas": daily_metric_deltas(samples, "G_norm", good_only=True),
        "since": since,
        "limit": limit,
        "series_id": series_id,
        "datasets": list_color_datasets(monitor_id),
    })


@plant_health_api.route("/monitors/<monitor_id>/watering", methods=["GET"])
def api_list_watering(monitor_id):
    import time as _time

    limit = min(200, int(request.args.get("limit", 50)))
    since = None
    if request.args.get("since"):
        try:
            since = float(request.args.get("since"))
        except (TypeError, ValueError):
            since = None
    elif request.args.get("hours"):
        try:
            hours = float(request.args.get("hours"))
            if hours > 0:
                since = _time.time() - hours * 3600.0
        except (TypeError, ValueError):
            since = None
    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({
        "ok": True,
        "events": list_watering_events(monitor_id, since=since, limit=limit),
    })


@plant_health_api.route("/monitors/<monitor_id>/watering", methods=["POST"])
def api_log_watering(monitor_id):
    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    watered_at = body.get("watered_at")
    try:
        watered_at_f = float(watered_at) if watered_at is not None else None
    except (TypeError, ValueError):
        watered_at_f = None
    event = save_watering_event(
        monitor_id,
        watered_at=watered_at_f,
        note=body.get("note"),
    )
    return jsonify({"ok": True, "event": event})


@plant_health_api.route("/monitors/<monitor_id>/mask-overlay", methods=["GET"])
def api_mask_overlay(monitor_id):
    """Live camera frame with the active plant mask tinted for UI display."""
    from flask import Response

    from basebuddy.plugins.plant_health.metrics import render_masked_frame_jpeg

    monitor = get_monitor(monitor_id)
    if not monitor:
        return jsonify({"ok": False, "error": "not found"}), 404
    try:
        camera_id = int(monitor.get("camera_id", 0))
        max_w = min(1280, max(320, int(request.args.get("w", 960))))
        frame = capture_frame_bgr(
            camera_id,
            max_width=max_w,
            crop=monitor.get("frame_crop"),
        )
        if frame is None:
            return jsonify({"ok": False, "error": "No frame from camera"}), 400
        refine_arg = request.args.get("green_refine")
        green_refine = None
        if refine_arg is not None and str(refine_arg) != "":
            try:
                green_refine = float(refine_arg)
            except (TypeError, ValueError):
                green_refine = None
        luma_arg = request.args.get("luma_carve")
        luma_carve = None
        if luma_arg is not None and str(luma_arg) != "":
            try:
                luma_carve = float(luma_arg)
            except (TypeError, ValueError):
                luma_carve = None
        data, metrics, err = render_masked_frame_jpeg(
            frame, monitor, green_refine=green_refine, luma_carve=luma_carve
        )
        if err or not data:
            return jsonify({"ok": False, "error": err or "Failed"}), 400
        resp = Response(data, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        if metrics:
            resp.headers["X-Plant-Coverage"] = str(metrics.get("coverage", ""))
            resp.headers["X-Plant-Greenness"] = str(metrics.get("greenness", ""))
            resp.headers["X-Plant-Pixels"] = str(metrics.get("plant_pixels", ""))
        return resp
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@plant_health_api.route("/monitors/<monitor_id>/live-frame", methods=["GET"])
def api_live_frame(monitor_id):
    """Live camera JPEG, cropped to the monitor frame_crop when set."""
    from flask import Response

    from basebuddy.plugins.plant_health.frame_crop import normalize_crop

    monitor = get_monitor(monitor_id)
    if not monitor:
        return jsonify({"ok": False, "error": "not found"}), 404
    try:
        camera_id = int(monitor.get("camera_id", 0))
        max_w = min(1280, max(320, int(request.args.get("w", 960))))
        crop = monitor.get("frame_crop")
        if request.args.get("full") in ("1", "true", "yes"):
            crop = None
        override = request.args.get("crop")
        if override:
            try:
                import json as _json

                crop = normalize_crop(_json.loads(override))
            except Exception:
                pass
        frame = capture_frame_bgr(camera_id, max_width=max_w, crop=crop)
        if frame is None:
            return jsonify({"ok": False, "error": "No frame from camera"}), 400
        import cv2

        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            return jsonify({"ok": False, "error": "Encode failed"}), 400
        resp = Response(buf.tobytes(), mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


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
        green_refine = body.get("green_refine", 0)
        luma_carve = body.get("luma_carve", 0)
        data, err = render_preview_with_points(
            camera_id, points, labels,
            green_refine=green_refine,
            luma_carve=luma_carve,
            crop=body.get("frame_crop"),
        )
        if err:
            return jsonify({"ok": False, "error": err}), 400
        return Response(data, mimetype="image/jpeg")
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@plant_health_api.route("/segment/save", methods=["POST"])
def api_segment_save():
    try:
        body = request.get_json(silent=True) or {}
        monitor_id = body.get("monitor_id") or ""
        if "frame_crop" in body:
            crop_arg = body.get("frame_crop")
        else:
            mon = get_monitor(monitor_id)
            crop_arg = (mon or {}).get("frame_crop")
        reanalyze = bool(body.get("reanalyze") or body.get("reanalyze_history"))
        result = save_segmentation_pattern(
            monitor_id,
            int(body.get("camera_id", 0)),
            body.get("points") or [],
            body.get("labels") or [],
            force_new_series=bool(body.get("force_new") or body.get("new_series") or reanalyze),
            frame_crop=crop_arg,
            reanalyze=reanalyze,
        )
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── SAM growth series ──────────────────────────────────────────────


@plant_health_api.route("/monitors/<monitor_id>/sam-series", methods=["GET"])
def api_list_sam_series(monitor_id):
    from basebuddy.plugins.plant_health.sam_growth import ensure_series_from_existing_mask
    from basebuddy.plugins.plant_health.sam_series import get_active_series, list_series

    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    series = list_series(monitor_id)
    bootstrapped = None
    if not series:
        try:
            bootstrapped = ensure_series_from_existing_mask(monitor_id)
            if bootstrapped:
                series = list_series(monitor_id)
        except Exception:
            bootstrapped = None
    active = get_active_series(monitor_id)
    monitor = get_monitor(monitor_id)
    from basebuddy.plugins.plant_health.sam_growth import growth_config

    growth_cfg = growth_config(monitor or {})
    seg = (monitor or {}).get("segmentation") or {}
    has_saved_mask = bool(
        seg.get("mask_path")
        or seg.get("mode") == "color_profile"
        or (active and active.get("initial_mask_path"))
        or any(s.get("initial_mask_path") for s in series)
    )
    payload = {
        "ok": True,
        "series": series,
        "active_series_id": active["id"] if active else None,
        "sam_growth": growth_cfg,
        "has_saved_mask": has_saved_mask,
    }
    if bootstrapped:
        payload["bootstrapped"] = True
        payload["has_anchor_points"] = bool(bootstrapped.get("has_anchor_points"))
    elif active:
        payload["has_anchor_points"] = bool(active.get("points"))
    return jsonify(payload)


@plant_health_api.route("/monitors/<monitor_id>/sam-series/<int:series_id>", methods=["GET"])
def api_get_sam_series(monitor_id, series_id):
    import time as _time

    from basebuddy.plugins.plant_health.mask_gif import mask_gif_path
    from basebuddy.plugins.plant_health.sam_series import (
        get_series,
        list_exemplars,
        list_growth_samples,
        sample_window,
    )

    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    series = get_series(series_id)
    if not series or series.get("monitor_id") != monitor_id:
        return jsonify({"ok": False, "error": "series not found"}), 404
    limit, since = sample_window(request.args, now=_time.time())
    samples = list_growth_samples(series_id, limit=limit, since=since)
    exemplars = list_exemplars(series_id, limit=60)
    gif_file = mask_gif_path(int(series.get("camera_id") or 0), series_id)
    mask_gif = None
    if os.path.isfile(gif_file):
        mask_gif = {
            "url": "/" + gif_file.replace("\\", "/"),
            "path": gif_file,
        }
    playback = []
    for s in samples:
        if not s.get("ok") or s.get("lights_out"):
            continue
        overlay = s.get("mask_path") if s.get("mask_path") and os.path.isfile(s["mask_path"]) else None
        silhouette = (
            s.get("silhouette_path")
            if s.get("silhouette_path") and os.path.isfile(s["silhouette_path"])
            else None
        )
        if overlay and not silhouette:
            try:
                from basebuddy.plugins.plant_health.mask_gif import (
                    ensure_silhouette_from_overlay,
                )

                silhouette = ensure_silhouette_from_overlay(overlay)
            except Exception:
                silhouette = None
        raw_cand = s.get("raw_path") or s.get("exemplar_path")
        raw = raw_cand if raw_cand and os.path.isfile(raw_cand) else None
        if not overlay and not raw:
            continue
        playback.append({
            "sampled_at": s.get("sampled_at"),
            "overlay_url": _still_url(overlay),
            "mask_url": _still_url(silhouette) or _still_url(overlay),
            "raw_url": _still_url(raw) or _still_url(overlay),
            "mask_area_px": s.get("mask_area_px"),
            "green_pixels_in_mask": s.get("green_pixels_in_mask"),
            "green_fraction_in_mask": s.get("green_fraction_in_mask"),
        })
    return jsonify({
        "ok": True,
        "series": series,
        "samples": samples,
        "exemplars": exemplars,
        "mask_gif": mask_gif,
        "playback": playback,
    })


@plant_health_api.route(
    "/monitors/<monitor_id>/sam-series/<int:series_id>/mask-gif",
    methods=["GET"],
)
def api_sam_series_mask_gif(monitor_id, series_id):
    """Return (and optionally rebuild) the mask-evolution GIF for a series."""
    from basebuddy.plugins.plant_health.mask_gif import (
        ensure_series_mask_gif,
        mask_gif_path,
    )
    from basebuddy.plugins.plant_health.sam_series import get_series

    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    series = get_series(series_id)
    if not series or series.get("monitor_id") != monitor_id:
        return jsonify({"ok": False, "error": "series not found"}), 404

    force = str(request.args.get("rebuild", "")).lower() in ("1", "true", "yes")
    meta_only = str(request.args.get("meta", "")).lower() in ("1", "true", "yes")
    camera_id = int(series.get("camera_id") or 0)
    result = ensure_series_mask_gif(series_id, camera_id, force=force)
    if not result.get("ok"):
        return jsonify(result), 404
    path = result.get("path") or mask_gif_path(camera_id, series_id)
    if meta_only:
        return jsonify({
            "ok": True,
            "url": result.get("url") or ("/" + path.replace("\\", "/")),
            "frame_count": result.get("frame_count"),
            "cached": result.get("cached"),
            "bytes": result.get("bytes"),
            "series_id": series_id,
            "label": series.get("label"),
        })
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "GIF not found"}), 404
    return send_file(path, mimetype="image/gif", conditional=True)


@plant_health_api.route(
    "/monitors/<monitor_id>/sam-series/<int:series_id>/export",
    methods=["POST"],
)
def api_export_sam_playback(monitor_id, series_id):
    """Stitch overlay, mask, calendar, and plots into a GIF or MOV."""
    import time as _time

    from basebuddy.plugins.plant_health.playback_export import build_playback_export
    from basebuddy.plugins.plant_health.sam_series import (
        get_series,
        list_growth_samples,
        sample_window,
    )

    monitor = get_monitor(monitor_id)
    if not monitor:
        return jsonify({"ok": False, "error": "not found"}), 404
    series = get_series(series_id)
    if not series or series.get("monitor_id") != monitor_id:
        return jsonify({"ok": False, "error": "series not found"}), 404

    body = request.get_json(silent=True) or {}
    fmt = str(body.get("format") or request.args.get("format") or "gif").lower()
    try:
        speed = float(body.get("speed") or request.args.get("speed") or 1)
    except (TypeError, ValueError):
        speed = 1.0
    max_frames = body.get("max_frames")
    if max_frames is not None:
        try:
            max_frames = int(max_frames)
        except (TypeError, ValueError):
            max_frames = None
    plant = str(monitor.get("name") or "plant").strip() or "plant"
    limit, since = sample_window(body, now=_time.time())
    samples = list_growth_samples(series_id, limit=limit, since=since)
    result = build_playback_export(
        samples,
        fmt=fmt,
        speed=speed,
        title=plant,
        max_frames=max_frames,
    )
    if not result.get("ok"):
        return jsonify(result), 400
    path = result["path"]

    @after_this_request
    def _cleanup(response):
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        return response

    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in plant.lower())[:48]
    download_name = f"{safe or 'plant'}_growth.{ 'gif' if fmt == 'gif' else 'mov' }"
    mime = "image/gif" if fmt == "gif" else "video/quicktime"
    return send_file(
        path,
        mimetype=mime,
        as_attachment=True,
        download_name=download_name,
        max_age=0,
    )


@plant_health_api.route("/monitors/<monitor_id>/sam-series/<int:series_id>/activate", methods=["POST"])
def api_activate_sam_series(monitor_id, series_id):
    from basebuddy.plugins.plant_health.sam_series import activate_series, get_series

    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    series = get_series(series_id)
    if not series or series.get("monitor_id") != monitor_id:
        return jsonify({"ok": False, "error": "series not found"}), 404
    updated = activate_series(series_id)
    return jsonify({"ok": True, "series": updated})


@plant_health_api.route("/monitors/<monitor_id>/sam-series", methods=["POST"])
def api_create_sam_series(monitor_id):
    """Create a new growth series from explicit points (or current editor payload)."""
    from basebuddy.plugins.plant_health.sam_growth import create_series_from_save

    monitor = get_monitor(monitor_id)
    if not monitor:
        return jsonify({"ok": False, "error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    points = body.get("points") or []
    labels = body.get("labels") or []
    if not points or not any(int(l) == 1 for l in labels):
        return jsonify({"ok": False, "error": "Need foreground SAM points"}), 400
    camera_id = int(body.get("camera_id", monitor.get("camera_id", 0)))
    result = create_series_from_save(
        monitor_id,
        camera_id,
        points,
        labels,
        frame_w=body.get("frame_w"),
        frame_h=body.get("frame_h"),
        initial_mask_path=body.get("mask_path"),
        take_first_sample=bool(body.get("sample", True)),
        force_new=bool(body.get("force_new") or body.get("new_series")),
    )
    return jsonify(result)


@plant_health_api.route(
    "/monitors/<monitor_id>/sam-series/<int:series_id>/apply-carves",
    methods=["POST"],
)
def api_apply_carves(monitor_id, series_id):
    from basebuddy.plugins.plant_health.sam_growth import apply_carves_to_series

    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    result = apply_carves_to_series(monitor_id, series_id)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@plant_health_api.route("/monitors/<monitor_id>/sam-growth/sample", methods=["POST"])
def api_sam_growth_sample(monitor_id):
    """Manual 'Run growth sample now'."""
    from basebuddy.plugins.plant_health.sam_growth import run_growth_sample

    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    series_id = body.get("series_id")
    result = run_growth_sample(
        monitor_id,
        series_id=int(series_id) if series_id is not None else None,
        force=True,
        trigger="manual",
    )
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@plant_health_api.route("/monitors/<monitor_id>/sam-growth/config", methods=["POST"])
def api_sam_growth_config(monitor_id):
    """Update sam_growth schedule settings on the monitor."""
    monitor = get_monitor(monitor_id)
    if not monitor:
        return jsonify({"ok": False, "error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    current = dict(monitor.get("sam_growth") or {})
    for key in (
        "enabled", "interval_s", "exemplar_interval_s", "area_change_ratio",
        "green_refine", "luma_carve",
    ):
        if key in body:
            current[key] = body[key]
    if "interval_s" in current:
        current["interval_s"] = max(60, int(current["interval_s"]))
    if "exemplar_interval_s" in current:
        current["exemplar_interval_s"] = max(60, int(current["exemplar_interval_s"]))
    if "green_refine" in current:
        from basebuddy.plugins.plant_health.green_refine import clamp_threshold

        current["green_refine"] = clamp_threshold(current["green_refine"])
    if "luma_carve" in current:
        from basebuddy.plugins.plant_health.green_refine import clamp_luma

        current["luma_carve"] = clamp_luma(current["luma_carve"])
    updated = update_monitor(monitor_id, {"sam_growth": current})
    return jsonify({"ok": True, "sam_growth": (updated or {}).get("sam_growth")})


@plant_health_api.route("/monitors/<monitor_id>/reanalyze", methods=["GET"])
def api_reanalyze_status(monitor_id):
    from basebuddy.plugins.plant_health.reanalyze import get_reanalyze_job

    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    job = get_reanalyze_job(monitor_id)
    return jsonify({"ok": True, "job": job})


@plant_health_api.route("/monitors/<monitor_id>/reanalyze", methods=["POST"])
def api_reanalyze_history(monitor_id):
    from basebuddy.plugins.plant_health.reanalyze import start_history_reanalyze

    if not get_monitor(monitor_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    target = body.get("series_id") or body.get("target_series_id")
    try:
        target_id = int(target) if target is not None else None
    except (TypeError, ValueError):
        target_id = None
    job = start_history_reanalyze(monitor_id, target_series_id=target_id)
    return jsonify({"ok": True, "job": job})
