"""
Traffic Analytics Plugin.

Optional plugin for vehicle/pedestrian traffic monitoring and analysis.
"""


def register_traffic_analytics(app):
    """
    Register traffic analytics plugin with the Flask app.
    
    Args:
        app: Flask application instance
    """
    from basebuddy.plugins.traffic_analytics.api.traffic import traffic_api
    app.register_blueprint(traffic_api, url_prefix='/api/traffic')
    
    app.logger.info("Traffic analytics plugin registered")

