"""Traffic analytics page."""
from flask import render_template

from basebuddy.pages.traffic import traffic_page_bp


@traffic_page_bp.route("/traffic")
def traffic_page():
    from basebuddy.modules.config import PX_PER_M

    return render_template(
        "traffic.html",
        active_page="traffic",
        px_per_m=PX_PER_M,
    )
