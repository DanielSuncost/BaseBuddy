"""
Optional premium (managed cloud) integration.

The open-source tree ships BYO S3/R2 only. Hosted premium installs a separate
private package, e.g. `basebuddy_premium`, that registers managed cloud storage.

Recommended layout (two repos, same app):
  basebuddy/          — public OSS (this repo)
  basebuddy-premium/  — private pip package imported at runtime

Keep premium as a thin overlay; never fork core UI — only extend via these hooks.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _try_import(module_name: str):
    try:
        import importlib

        return importlib.import_module(module_name)
    except ImportError:
        return None
    except Exception as exc:
        logger.warning("Premium module %s unavailable: %s", module_name, exc)
        return None


def _oss_managed_cloud_defaults() -> Dict[str, Any]:
    from basebuddy.core.premium_content import (
        ARTIFACT_SIZE_HINTS,
        CLOUD_BUFFER_EXPLAIN,
        LOST_KEY_HELP_OSS,
        MANAGED_CLOUD_FEATURES,
        MANAGED_CLOUD_INFERENCE_FEATURES,
        MANAGED_CLOUD_TAGLINE,
        PRICING_SUMMARY_OSS,
        PRICING_TIERS,
    )

    return {
        "available": False,
        "active": False,
        "headline": "BaseBuddy Cloud",
        "tagline": MANAGED_CLOUD_TAGLINE,
        "description": (
            "Subscribe for cloud storage with a rolling buffer. Paste one API key below — "
            "we handle the bucket, quota, and cleanup."
        ),
        "cta_label": "View plans & pricing",
        "cta_url": "https://basebuddy.app/pricing",
        "signup_url": "https://basebuddy.app/signup",
        "account_url": "https://basebuddy.app/account",
        "support_url": "https://basebuddy.app/support",
        "lost_key_help": LOST_KEY_HELP_OSS,
        "pricing_summary": PRICING_SUMMARY_OSS,
        "pricing_tiers": PRICING_TIERS,
        "features": MANAGED_CLOUD_FEATURES,
        "inference_features": MANAGED_CLOUD_INFERENCE_FEATURES,
        "cloud_buffer_explain": CLOUD_BUFFER_EXPLAIN,
        "quota_policy": CLOUD_BUFFER_EXPLAIN,
        "how_it_works": CLOUD_BUFFER_EXPLAIN,
        "artifact_hints": ARTIFACT_SIZE_HINTS,
        "compare_note": (
            "Bring your own bucket (free, self-hosted) uses your S3/R2 credentials. "
            "BaseBuddy Cloud (premium) uses our hosted API — simpler, with support and billing included."
        ),
    }


def get_edition_info() -> Dict[str, Any]:
    """Edition metadata for the storage UI."""
    premium = _try_import("basebuddy_premium")
    if premium and hasattr(premium, "edition_info"):
        info = premium.edition_info()
        if isinstance(info, dict):
            return info

    return {
        "edition": "open_source",
        "managed_cloud": _oss_managed_cloud_defaults(),
    }


def premium_nav_links() -> List[Dict[str, str]]:
    """
    Optional extra nav items when premium package is installed.
    OSS default: no extra menu (everything lives under Storage).
    """
    mod = _try_import("basebuddy_premium.nav")
    if mod and hasattr(mod, "nav_links"):
        return mod.nav_links()
    mc = get_edition_info().get("managed_cloud") or {}
    if mc.get("active"):
        return [
            {
                "href": mc.get("account_url") or "/storage#cloud-account",
                "label": "Cloud account",
                "icon": "cloud",
            }
        ]
    return []


def register_premium_blueprints(app) -> None:
    """Register premium-only Flask routes (e.g. /account) if package is installed."""
    mod = _try_import("basebuddy_premium.flask_routes")
    if mod and hasattr(mod, "register"):
        mod.register(app)


def get_managed_cloud_backend():
    """Return a managed-cloud ObjectStorageBackend or None (OSS default)."""
    mod = _try_import("basebuddy_premium.managed_cloud")
    if mod and hasattr(mod, "get_backend"):
        return mod.get_backend()
    return None


def get_plant_health_analyzer():
    """
    Plant vision analyzer. Premium: species classifier + tailored LLM prompts.
    OSS: user-supplied OpenAI-compatible vision API key.
    """
    mod = _try_import("basebuddy_premium.plant_health")
    if mod and hasattr(mod, "get_analyzer"):
        return mod.get_analyzer()
    from basebuddy.plugins.plant_health.analyzer import OSSPlantAnalyzer
    return OSSPlantAnalyzer()


def plant_health_premium_available() -> bool:
    mod = _try_import("basebuddy_premium.plant_health")
    return mod is not None and hasattr(mod, "get_analyzer")


def resolve_remote_backend(user_backend):
    """
    Pick active remote backend: managed premium wins when enabled, else BYO user backend.
    """
    from basebuddy.modules.config import BASEBUDDY_MANAGED_CLOUD_ENABLED

    managed = get_managed_cloud_backend()
    if (
        BASEBUDDY_MANAGED_CLOUD_ENABLED
        and managed is not None
        and getattr(managed, "is_active", False)
    ):
        return managed, "managed"
    if user_backend is not None and getattr(user_backend, "is_configured", False):
        return user_backend, "byo"
    return None, "none"


def reload_managed_cloud_from_config() -> None:
    """Push config.txt cloud settings into the premium client (if installed)."""
    mod = _try_import("basebuddy_premium.managed_cloud")
    if not mod or not hasattr(mod, "reload_backend"):
        return
    from basebuddy.modules import config as cfg

    mod.reload_backend(cfg.BASEBUDDY_CLOUD_API_URL, cfg.BASEBUDDY_CLOUD_API_KEY)
