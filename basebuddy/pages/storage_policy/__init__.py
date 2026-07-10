"""
Storage & backup policy page — configure external drives, quotas, and offload behavior.
"""
from flask import Blueprint

storage_policy_bp = Blueprint("storage_policy", __name__)

from basebuddy.pages.storage_policy import routes  # noqa: E402, F401
from basebuddy.pages.storage_policy import api  # noqa: E402, F401
