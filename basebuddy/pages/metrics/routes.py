"""System metrics and analytics page."""
import os

from flask import render_template

from basebuddy.pages.metrics import metrics_bp


@metrics_bp.route("/metrics")
def metrics_page():
    try:
        import basebuddy.modules.state as shared_state
        from basebuddy.core.api.metrics import get_system_metrics
        from basebuddy.modules.config import CAM_URLS, RECORD_ROOT

        def calculate_recording_size():
            total = 0
            if os.path.exists(RECORD_ROOT):
                for dirpath, _dirnames, filenames in os.walk(RECORD_ROOT):
                    for f in filenames:
                        try:
                            total += os.path.getsize(os.path.join(dirpath, f))
                        except OSError:
                            pass
            return total

        metrics = get_system_metrics(
            shared_state.grabbers,
            shared_state.analytics_db,
            CAM_URLS,
            calculate_recording_size,
        )
        if "timestamp_formatted" in metrics:
            metrics["timestamp"] = metrics["timestamp_formatted"]

        from basebuddy.modules.camera_profiles import get_profile_manager

        profile_manager = get_profile_manager()
        camera_names = {}
        for cam_id in metrics.get("cameras", {}).keys():
            profile = profile_manager.get_profile(cam_id)
            if profile and profile.name:
                camera_names[cam_id] = profile.name

        return render_template(
            "metrics.html",
            active_page="metrics",
            metrics=metrics,
            camera_names=camera_names,
        )
    except Exception as e:
        return render_template(
            "metrics.html",
            active_page="metrics",
            error=str(e),
            metrics={},
            camera_names={},
        )
