"""Plant condition monitoring — OSS vision API + premium hook."""
from __future__ import annotations


def register_plant_health(app):
    from basebuddy.plugins.plant_health.api import plant_health_api
    from basebuddy.plugins.plant_health.db import init_plant_tables
    from basebuddy.plugins.plant_health.scheduler import get_plant_scheduler
    import basebuddy.modules.state as shared_state

    if shared_state.analytics_db:
        with shared_state.analytics_db._connect() as conn:
            init_plant_tables(conn)

    app.register_blueprint(plant_health_api, url_prefix="/api/plants")
    get_plant_scheduler().start()
    from basebuddy.plugins.plant_health.blogger_scheduler import get_blogger_scheduler
    get_blogger_scheduler().start()
    app.logger.info("Plant health plugin registered")
