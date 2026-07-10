"""
Pages module - One folder per UI page.

Each page folder contains:
- __init__.py: Blueprint definition
- routes.py: HTML page routes
- api.py: Page-specific API endpoints

This module registers all page blueprints with the Flask app.
"""
import logging

logger = logging.getLogger(__name__)


def register_pages(app):
    """
    Register all page blueprints with the Flask app.
    
    Args:
        app: Flask application instance
    """
    from basebuddy.pages.camera_wall import camera_wall_bp
    from basebuddy.pages.camera_detail import camera_detail_bp
    from basebuddy.pages.recordings import recordings_bp
    from basebuddy.pages.timelapse import timelapse_bp
    from basebuddy.pages.gallery import gallery_bp
    from basebuddy.pages.config import config_bp
    from basebuddy.pages.storage_policy import storage_policy_bp
    from basebuddy.pages.scenes import scenes_bp
    from basebuddy.pages.plants import plants_bp
    from basebuddy.pages.metrics import metrics_bp
    from basebuddy.pages.events import events_bp
    from basebuddy.pages.integrations import integrations_bp
    from basebuddy.pages.setup import setup_bp
    from basebuddy.pages.training import training_bp
    from basebuddy.pages.traffic import traffic_page_bp
    
    # Register page blueprints
    app.register_blueprint(camera_wall_bp)
    app.register_blueprint(camera_detail_bp)
    app.register_blueprint(recordings_bp)
    app.register_blueprint(timelapse_bp)
    app.register_blueprint(gallery_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(storage_policy_bp)
    app.register_blueprint(scenes_bp)
    app.register_blueprint(plants_bp)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(integrations_bp)
    app.register_blueprint(setup_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(traffic_page_bp)

    logger.info("All page blueprints registered")
