"""
Recordings page routes.
"""
from datetime import datetime

from flask import render_template, request

from basebuddy.pages.recordings import recordings_bp
from basebuddy.pages.recordings.api import (
    format_range_label,
    get_camera_summaries_for_range,
    get_recordings_for_range,
    parse_date_range,
)


def _query_int(name: str):
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@recordings_bp.route("/recordings")
def recordings_page():
    """Recording playback page with clip grid, camera grid, and range filters."""
    try:
        view = request.args.get("view", "clips")
        if view not in ("clips", "cameras"):
            view = "clips"

        start, end, range_preset = parse_date_range(
            range_preset=request.args.get("range"),
            date_from=request.args.get("from"),
            date_to=request.args.get("to"),
            legacy_date=request.args.get("date"),
        )
        camera_id = _query_int("cam")

        recordings = get_recordings_for_range(start, end, camera_id=camera_id)
        camera_summaries = get_camera_summaries_for_range(start, end)

        range_label = format_range_label(start, end, range_preset)
        today = datetime.now().strftime("%Y-%m-%d")

        return render_template(
            "recordings.html",
            active_page="recordings",
            view=view,
            range_preset=range_preset,
            range_label=range_label,
            date_from=start.isoformat(),
            date_to=end.isoformat(),
            camera_id=camera_id,
            recordings=recordings,
            camera_summaries=camera_summaries,
            recording_count=len(recordings),
            camera_count=len(camera_summaries),
            today=today,
        )
    except Exception as e:
        today = datetime.now().strftime("%Y-%m-%d")
        return render_template(
            "recordings.html",
            active_page="recordings",
            view="clips",
            range_preset="today",
            range_label="Today",
            date_from=today,
            date_to=today,
            camera_id=None,
            recordings=[],
            camera_summaries=[],
            error=str(e),
            recording_count=0,
            camera_count=0,
            today=today,
        )
