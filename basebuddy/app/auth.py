"""Optional HTTP basic auth for the web UI."""
from __future__ import annotations

import hmac

from flask import Response, request


def register_auth(app):
    from basebuddy.modules.config import ADMIN_PASSWORD, ADMIN_USERNAME, AUTH_ENABLE, HOST

    if not AUTH_ENABLE:
        if HOST in ("0.0.0.0", "::"):
            app.logger.warning(
                "Authentication is DISABLED and the server binds to %s — every device "
                "on the network can view cameras and change settings. Set AUTH_ENABLE=true "
                "and ADMIN_PASSWORD in .env unless this host is on a trusted, isolated network.",
                HOST,
            )
        return

    if not ADMIN_PASSWORD:
        app.logger.warning(
            "AUTH_ENABLE is true but ADMIN_PASSWORD is empty — auth disabled. "
            "Set ADMIN_PASSWORD in .env for production."
        )
        return

    public_paths = ("/health",)

    @app.before_request
    def require_auth():
        if request.path.startswith("/static"):
            return None
        if request.path in public_paths:
            return None
        auth = request.authorization
        if (
            auth
            and hmac.compare_digest(auth.username or "", ADMIN_USERNAME)
            and hmac.compare_digest(auth.password or "", ADMIN_PASSWORD)
        ):
            return None
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="BaseBuddy"'},
        )

    app.logger.info("HTTP basic auth enabled for web UI")
