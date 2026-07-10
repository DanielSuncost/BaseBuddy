from flask import render_template
from basebuddy.pages.events import events_bp


@events_bp.route("/events")
def events_page():
    return render_template("events.html", active_page="events")
