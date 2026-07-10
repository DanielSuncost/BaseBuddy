from flask import render_template

from basebuddy.pages.training import training_bp


@training_bp.route("/training")
def training_page():
    from basebuddy.modules.config import INFERENCE_CLOUD_API_KEY, INFERENCE_CLOUD_ENDPOINT

    return render_template(
        "training.html",
        active_page="training",
        cloud_configured=bool(INFERENCE_CLOUD_API_KEY and INFERENCE_CLOUD_ENDPOINT),
    )
