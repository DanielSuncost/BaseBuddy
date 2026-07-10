"""HTML route for the storage policy UI."""
from flask import render_template

from basebuddy.pages.storage_policy import storage_policy_bp


@storage_policy_bp.route("/storage")
def storage_policy_page():
    return render_template(
        "storage_policy.html",
        active_page="storage",
    )
