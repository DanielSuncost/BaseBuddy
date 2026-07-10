"""
Gallery page routes.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from flask import render_template, request

from basebuddy.pages.gallery import gallery_bp


def _parse_camera_filter(cam_param: Optional[str]) -> Optional[List[int]]:
    if not cam_param:
        return None
    ids = []
    for part in cam_param.split(","):
        try:
            val = int(part.strip())
            if val > 0:
                ids.append(val - 1)
        except ValueError:
            continue
    return sorted(set(ids)) if ids else None


def _camera_display_names(camera_ids: List[int]) -> dict:
    names: dict = {}
    try:
        from basebuddy.modules.camera_profiles import get_profile_manager

        manager = get_profile_manager()
        for cid in camera_ids:
            profile = manager.get_profile(cid)
            if profile and profile.name:
                names[cid] = profile.name
    except Exception:
        pass
    return names


def _gallery_camera_options(analytics_db, view_mode: str, recent_hours: int, requested_date: str) -> list[dict]:
    if view_mode == "date":
        rows = analytics_db.get_cameras_with_detections(date=requested_date)
    else:
        rows = analytics_db.get_cameras_with_detections(hours=recent_hours)

    ids = [r["camera_id"] for r in rows]
    names = _camera_display_names(ids)
    options = []
    for row in rows:
        cid = row["camera_id"]
        label = names.get(cid) or f"Camera {cid + 1}"
        options.append(
            {
                "id": cid,
                "label": label,
                "count": row["count"],
                "value": str(cid + 1),
            }
        )
    return options


@gallery_bp.route("/gallery")
def gallery_page():
    """Detection gallery page - shows detected objects from database"""
    try:
        import basebuddy.modules.state as shared_state

        analytics_db = shared_state.analytics_db

        view_mode = request.args.get("view", "recent")
        requested_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
        class_filter = request.args.get("class") or None
        show_similar = request.args.get("show_similar", "false") == "true"
        detection_id = request.args.get("detection_id")

        page = max(request.args.get("page", 1, type=int), 1)
        per_page = min(max(request.args.get("per_page", 50, type=int), 1), 200)
        recent_hours = int(request.args.get("hours", "1"))

        cam_param = request.args.get("cam") or ""
        selected_camera_ids = _parse_camera_filter(cam_param or None)
        db_camera_ids = selected_camera_ids
        single_camera = selected_camera_ids[0] if selected_camera_ids and len(selected_camera_ids) == 1 else None

        if view_mode == "date":
            available_classes = (
                analytics_db.get_available_classes_for_date(requested_date) if analytics_db else []
            )
        else:
            available_classes = (
                analytics_db.get_available_classes(hours=recent_hours) if analytics_db else []
            )

        gallery_cameras = _gallery_camera_options(analytics_db, view_mode, recent_hours, requested_date)

        detections = []
        total_items = 0
        total_pages = 1

        if view_mode == "recent":
            if not show_similar:
                result = analytics_db.get_unique_detections(
                    hours=recent_hours,
                    camera_ids=db_camera_ids,
                    page=page,
                    per_page=per_page,
                    class_filter=class_filter,
                )
                detections = result["items"]
                total_items = result["total"]
                total_pages = result["total_pages"]
            else:
                if class_filter:
                    detections = analytics_db.get_recent_detections_by_class(
                        class_filter, hours=recent_hours, camera_id=single_camera, limit=per_page
                    )
                else:
                    detections = analytics_db.get_recent_detections(
                        hours=recent_hours, camera_id=single_camera, limit=per_page
                    )
                if db_camera_ids and not single_camera:
                    detections = [d for d in detections if d.get("camera_id") in db_camera_ids]
                total_items = len(detections)
                total_pages = 1

            view_title = f"Recent {class_filter.title() if class_filter else 'Detections'} (Last {recent_hours} hour{'s' if recent_hours != 1 else ''})"

        elif view_mode == "similar" and detection_id:
            detections = analytics_db.get_similar_detections(int(detection_id), position_threshold=50)
            if db_camera_ids:
                detections = [d for d in detections if d.get("camera_id") in db_camera_ids]
            total_items = len(detections)
            start_idx = (page - 1) * per_page
            detections = detections[start_idx : start_idx + per_page]
            total_pages = (total_items + per_page - 1) // per_page
            view_title = "Similar Detections"

        else:
            if not show_similar:
                result = analytics_db.get_unique_detections_for_date(
                    requested_date,
                    camera_ids=db_camera_ids,
                    page=page,
                    per_page=per_page,
                    class_filter=class_filter,
                )
                detections = result["items"]
                total_items = result["total"]
                total_pages = result["total_pages"]
            else:
                if class_filter:
                    detections = analytics_db.get_detection_events_for_date_by_class(
                        requested_date, class_filter, camera_id=single_camera, limit=per_page
                    )
                else:
                    detections = analytics_db.get_detection_events_for_date(
                        requested_date, camera_id=single_camera, limit=per_page
                    )
                if db_camera_ids and not single_camera:
                    detections = [d for d in detections if d.get("camera_id") in db_camera_ids]
                total_items = len(detections)
                total_pages = 1

            current_date = datetime.strptime(requested_date, "%Y-%m-%d")
            view_title = f"{class_filter.title() if class_filter else 'Detections'} for {current_date.strftime('%B %d, %Y')}"

        if view_mode == "recent" or view_mode == "similar":
            today = datetime.now().strftime("%Y-%m-%d")
            current_date = datetime.strptime(today, "%Y-%m-%d")
        else:
            current_date = datetime.strptime(requested_date, "%Y-%m-%d")

        prev_date = (current_date - timedelta(days=1)).strftime("%Y-%m-%d")
        next_date = (current_date + timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")

        show_calendar = view_mode == "date"
        calendar_month_title = None
        calendar_cells = []
        if show_calendar:
            cd = datetime.strptime(requested_date, "%Y-%m-%d")
            year, month = cd.year, cd.month
            first_day = datetime(year, month, 1)
            next_month = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
            last_day = next_month - timedelta(days=1)
            start_s = first_day.strftime("%Y-%m-%d")
            end_s = last_day.strftime("%Y-%m-%d")
            daily_counts = analytics_db.get_daily_detection_counts(start_s, end_s) if analytics_db else {}
            calendar_month_title = first_day.strftime("%B %Y")
            lead = first_day.weekday()
            calendar_cells = [None] * lead
            for day in range(1, last_day.day + 1):
                date_str = f"{year:04d}-{month:02d}-{day:02d}"
                calendar_cells.append(
                    {
                        "day": day,
                        "date_str": date_str,
                        "count": daily_counts.get(date_str, 0),
                        "selected": date_str == requested_date,
                        "is_today": date_str == today,
                    }
                )
            while len(calendar_cells) % 7 != 0:
                calendar_cells.append(None)

        for det in detections:
            try:
                ts = det.get("timestamp", "")
                if isinstance(ts, str):
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        dt = datetime.now()
                    det["human_timestamp"] = dt.strftime("%B %d, %Y at %I:%M %p")
                    hours_ago = (
                        datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)
                    ).total_seconds() / 3600
                    if hours_ago < 1:
                        det["relative_time"] = "just now"
                    elif hours_ago < 24:
                        det["relative_time"] = f"{int(hours_ago)} hours ago"
                    else:
                        det["relative_time"] = f"{int(hours_ago / 24)} days ago"
                else:
                    det["human_timestamp"] = "Recent detection"
                    det["relative_time"] = "just now"
            except Exception:
                det["human_timestamp"] = "Recent detection"
                det["relative_time"] = "just now"

        return render_template(
            "gallery.html",
            active_page="gallery",
            view_mode=view_mode,
            view_title=view_title,
            detections=detections,
            total_items=total_items,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            class_filter=class_filter or "",
            available_classes=available_classes,
            show_similar=show_similar,
            recent_hours=recent_hours,
            requested_date=requested_date,
            prev_date=prev_date,
            next_date=next_date,
            today=today,
            selected_camera_ids=selected_camera_ids,
            cam_param=cam_param,
            gallery_cameras=gallery_cameras,
            show_calendar=show_calendar,
            calendar_month_title=calendar_month_title,
            calendar_cells=calendar_cells,
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return render_template(
            "gallery.html",
            active_page="gallery",
            view_title="Detection Gallery",
            detections=[],
            error=str(e),
            total_items=0,
            page=1,
            per_page=50,
            total_pages=1,
            show_calendar=False,
            calendar_month_title=None,
            calendar_cells=[],
            gallery_cameras=[],
            available_classes=[],
            class_filter="",
            cam_param="",
            view_mode="recent",
            recent_hours=1,
            requested_date=datetime.now().strftime("%Y-%m-%d"),
            show_similar=False,
        )
