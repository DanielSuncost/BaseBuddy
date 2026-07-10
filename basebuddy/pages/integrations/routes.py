from flask import render_template
from basebuddy.pages.integrations import integrations_bp


@integrations_bp.route("/integrations")
def integrations_page():
    return render_template("integrations.html", active_page="integrations")
