"""
Core API blueprints registration.

All core surveillance API endpoints are registered here.
"""


def register_core_apis(app):
    """
    Register all core API blueprints with the Flask app.
    
    Args:
        app: Flask application instance
    """
    # Import blueprints
    from basebuddy.core.api.metrics import metrics_api
    from basebuddy.core.api.storage import storage_api
    from basebuddy.core.api.backup import backup_api
    from basebuddy.core.api.recording import recording_api
    from basebuddy.core.api.video import video_api
    from basebuddy.core.api.gallery import gallery_api
    from basebuddy.core.api.tracking import tracking_api
    from basebuddy.core.api.thresholds import thresholds_api
    from basebuddy.core.api.classes import classes_api
    from basebuddy.core.api.ignored_detections import ignored_api
    from basebuddy.core.api.rois import rois_api
    from basebuddy.core.api.static_files import static_files_api
    from basebuddy.core.api.health import health_api
    from basebuddy.core.api.inference import inference_api
    from basebuddy.core.api.models import models_api
    from basebuddy.core.api.events import events_api
    from basebuddy.core.api.integrations import integrations_api
    
    # Register API blueprints
    app.register_blueprint(metrics_api, url_prefix='/api/metrics')
    app.register_blueprint(storage_api, url_prefix='/api/storage')
    app.register_blueprint(backup_api, url_prefix='/api/backup')
    app.register_blueprint(recording_api, url_prefix='/record')
    app.register_blueprint(video_api, url_prefix='/video')
    app.register_blueprint(gallery_api, url_prefix='/api/gallery')
    app.register_blueprint(tracking_api, url_prefix='/api/tracking')
    app.register_blueprint(thresholds_api, url_prefix='/api/thresholds')
    app.register_blueprint(classes_api, url_prefix='/api/classes')
    app.register_blueprint(ignored_api, url_prefix='/api/ignored-detections')
    app.register_blueprint(rois_api, url_prefix='/api/rois')
    app.register_blueprint(health_api)
    app.register_blueprint(inference_api, url_prefix='/api/inference')
    app.register_blueprint(models_api, url_prefix='/api/models')
    app.register_blueprint(events_api)
    app.register_blueprint(integrations_api)
    
    # Register static file serving (no prefix - top level routes)
    app.register_blueprint(static_files_api)
    
    app.logger.info("Core APIs registered")
