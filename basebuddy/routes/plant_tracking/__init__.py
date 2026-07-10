"""
Plant Tracking Routes - Segmentation and Movement Analysis
"""

import logging

from flask import Blueprint

logger = logging.getLogger(__name__)

plant_tracking_bp = Blueprint('plant_tracking', __name__)

from .helpers import (  # noqa: E402,F401
    STILLS_DIR,
    PROMPT_CONFIG_DIR,
    MASKS_DIR,
    SAM_CHECKPOINT,
    _safe_seg,
    _safe_still_path,
    get_sam_predictor,
)
from .segmentation import (  # noqa: E402,F401
    analyze_color_profile,
    apply_color_filter,
    analyze_prompt_pattern,
    apply_pattern,
    segment_with_prompts,
)
from .schedules import (  # noqa: E402,F401
    SCHEDULES_DIR,
    get_schedule_file,
    load_schedules,
    save_schedules,
)

# Import route modules so their view functions register on the blueprint.
# schedules is already imported above; capture, masks and analysis register here.
from . import capture, masks, analysis  # noqa: E402,F401
