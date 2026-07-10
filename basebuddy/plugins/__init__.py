"""
Plugin system for BaseBuddy.

Plugins extend the core surveillance functionality with additional features.
Premium/proprietary features are implemented as plugins.
"""


def register_plugins(app):
    """
    Register all enabled plugins with the Flask app.
    """
    raw = app.config.get('PLUGINS_ENABLED')
    if raw is None:
        enabled_plugins = ['traffic_analytics', 'training']
    elif isinstance(raw, str):
        enabled_plugins = [p.strip() for p in raw.split(',') if p.strip()]
    else:
        enabled_plugins = list(raw)
    
    if 'plant_tracking' in enabled_plugins:
        try:
            from basebuddy.plugins.plant_tracking import register_plant_tracking
            register_plant_tracking(app)
        except ImportError:
            app.logger.warning("Plant tracking plugin enabled but not installed")
    
    if 'traffic_analytics' in enabled_plugins:
        try:
            from basebuddy.plugins.traffic_analytics import register_traffic_analytics
            register_traffic_analytics(app)
        except ImportError:
            app.logger.warning("Traffic analytics plugin enabled but not installed")

    if 'training' in enabled_plugins:
        try:
            from basebuddy.plugins.training import register_training
            register_training(app)
        except ImportError as exc:
            app.logger.warning("Training plugin not loaded: %s", exc)

    from basebuddy.modules.config import HOME_SCENES_ENABLE
    if HOME_SCENES_ENABLE or 'home_scenes' in enabled_plugins:
        try:
            from basebuddy.plugins.home_scenes import register_home_scenes
            register_home_scenes(app)
        except ImportError as exc:
            app.logger.warning("Home scenes plugin not loaded: %s", exc)

    try:
        from basebuddy.plugins.plant_health import register_plant_health
        register_plant_health(app)
    except ImportError as exc:
        app.logger.warning("Plant health plugin not loaded: %s", exc)


