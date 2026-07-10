"""Home scenes plugin — pantry, fridge, and shelf monitoring."""
from __future__ import annotations


def register_home_scenes(app):
    from basebuddy.plugins.home_scenes.api.scenes import scenes_api
    from basebuddy.plugins.home_scenes.db import init_scene_tables
    import basebuddy.modules.state as shared_state

    if shared_state.analytics_db:
        with shared_state.analytics_db._connect() as conn:
            init_scene_tables(conn)

    app.register_blueprint(scenes_api, url_prefix="/api/scenes")
    app.logger.info("Home scenes plugin registered")
