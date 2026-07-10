"""Training plugin — dataset builder, local/cloud jobs."""
from __future__ import annotations


def register_training(app):
    from basebuddy.plugins.training.api import training_api
    from basebuddy.plugins.training.db import init_training_tables
    import basebuddy.modules.state as shared_state

    if shared_state.analytics_db:
        with shared_state.analytics_db._connect() as conn:
            init_training_tables(conn)

    app.register_blueprint(training_api, url_prefix="/api/training")
    app.logger.info("Training plugin registered")
