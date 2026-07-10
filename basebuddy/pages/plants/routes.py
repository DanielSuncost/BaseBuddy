from flask import render_template

from basebuddy.pages.plants import plants_bp


@plants_bp.route("/plants")
def plants_page():
    from basebuddy.core.premium_hooks import plant_health_premium_available
    return render_template(
        "plants.html",
        active_page="plants",
        plant_premium_available=plant_health_premium_available(),
    )
