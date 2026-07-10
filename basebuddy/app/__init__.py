"""
BaseBuddy Application Factory

Creates and configures the Flask application instance.
Supports multiple frontends (Python templates, React, Mobile).
"""
import os
from flask import Flask
from flask_cors import CORS
import logging

from basebuddy.core.paths import get_app_root

logger = logging.getLogger(__name__)


def _register_route_blueprints(app):
    """Register blueprints from routes/ (people, power, cameras, plant_tracking, resources, multiview_3d)."""
    blueprints = [
        ('basebuddy.routes.people', 'people_bp', 'People'),
        ('basebuddy.routes.power', 'power_bp', 'Power'),
        ('basebuddy.routes.cameras', 'cameras_bp', 'Cameras'),
        ('basebuddy.routes.plant_tracking', 'plant_tracking_bp', 'Plant tracking'),
        ('basebuddy.routes.resources', 'resources_bp', 'Resources'),
        ('basebuddy.routes.multiview_3d', 'multiview_3d_bp', 'Multiview 3D'),
    ]
    for module_path, attr, name in blueprints:
        try:
            mod = __import__(module_path, fromlist=[attr])
            bp = getattr(mod, attr)
            app.register_blueprint(bp)
            logger.info("%s blueprint registered", name)
        except Exception:
            logger.error("Could not load %s blueprint", name, exc_info=True)


def create_app(config_name='default'):
    """
    Application factory pattern.
    
    Args:
        config_name: Configuration profile to load ('default', 'development', 'production')
        
    Returns:
        Tuple of (Flask app instance, SocketIO instance)
    """
    app_root = get_app_root()
    app = Flask(
        __name__,
        template_folder=os.path.join(app_root, "templates"),
        static_folder=os.path.join(app_root, "static"),
    )
    
    # Basic Flask configuration
    from basebuddy.modules.config import SECRET_KEY as CONFIG_SECRET_KEY

    secret = CONFIG_SECRET_KEY or os.environ.get("FLASK_SECRET_KEY")
    if not secret:
        # Random per-process key: sessions won't survive restarts, but that beats
        # a hardcoded default that lets anyone forge session cookies.
        secret = os.urandom(32).hex()
        app.logger.warning(
            "No SECRET_KEY configured — using a random per-run key. "
            "Set SECRET_KEY in .env to keep sessions across restarts."
        )
    app.config['SECRET_KEY'] = secret
    app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max upload
    
    # CORS: explicit origins in production; dev defaults to permissive for local tooling
    cors_raw = os.environ.get("CORS_ORIGINS", "").strip()
    cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]
    is_production = os.environ.get("FLASK_ENV", "").lower() == "production"
    if cors_origins:
        CORS(app, origins=cors_origins)
        socketio_cors: list[str] | str = cors_origins
    elif is_production:
        # Same-origin UI — no wildcard cross-origin in production
        socketio_cors = []
    else:
        CORS(app)
        socketio_cors = "*"

    # Initialize SocketIO for WebSocket video streaming
    socketio = None
    try:
        from flask_socketio import SocketIO
        socketio = SocketIO(
            app,
            cors_allowed_origins=socketio_cors,
            async_mode='threading',
        )
        logger.info("SocketIO initialized")
    except Exception as e:
        logger.error(f"Failed to initialize SocketIO: {e}")
    
    # Register core API blueprints
    from basebuddy.core.api import register_core_apis
    register_core_apis(app)
    
    # Register enabled plugins
    from basebuddy.plugins import register_plugins
    register_plugins(app)
    
    # Register modular pages (each page has routes.py and api.py).
    # A failure here is a real bug — crash at startup instead of silently
    # serving a degraded UI.
    from basebuddy.pages import register_pages
    register_pages(app)
    logger.info("Modular pages registered")

    # Register route blueprints (cameras, power, people, etc.)
    _register_route_blueprints(app)

    from basebuddy.app.auth import register_auth
    register_auth(app)

    from basebuddy.core.premium_hooks import premium_nav_links, register_premium_blueprints
    from basebuddy.core.navigation import CONFIG_TABS, NAV_ITEMS

    register_premium_blueprints(app)

    @app.context_processor
    def inject_navigation():
        return {
            "premium_nav_links": premium_nav_links(),
            "nav_items": NAV_ITEMS,
            "config_tabs": CONFIG_TABS,
        }

    # Initialize services
    from basebuddy.core.services import initialize_services
    initialize_services(app)
    
    # Register SocketIO handlers for camera streaming
    if socketio:
        from basebuddy.web.websocket_handlers import register_socketio_handlers
        register_socketio_handlers(socketio, app)
    
    return app, socketio

