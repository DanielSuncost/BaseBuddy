from flask import redirect

from basebuddy.pages.setup import setup_bp


@setup_bp.route("/setup")
def setup_page_redirect():
    return redirect("/config/setup", code=301)
