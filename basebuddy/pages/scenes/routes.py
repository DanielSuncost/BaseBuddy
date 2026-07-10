"""Home scenes UI."""
from flask import render_template
from basebuddy.pages.scenes import scenes_bp


@scenes_bp.route("/scenes")
def scenes_page():
    return render_template("scenes.html", active_page="scenes")
