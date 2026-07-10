"""
Core utility functions.

Shared utility functions used across the core surveillance system.
"""

from .logging import setup_logging, setup_exception_handler, logger
from .system_health import (
    monitor_resources,
    start_resource_monitor,
    stop_resource_monitor,
    get_system_info
)
from .datetime_utils import (
    calculate_sunrise_sunset,
    format_duration,
    parse_timestamp_filename
)
from .image_utils import (
    draw_progress_meter,
    draw_clock_face,
    draw_text_with_background,
    create_thumbnail
)

__all__ = [
    # Logging
    'setup_logging',
    'setup_exception_handler',
    'logger',
    
    # Resource monitoring
    'monitor_resources',
    'start_resource_monitor',
    'get_system_info',
    
    # Date/time utilities
    'calculate_sunrise_sunset',
    'format_duration',
    'parse_timestamp_filename',
    
    # Image utilities
    'draw_progress_meter',
    'draw_clock_face',
    'draw_text_with_background',
    'create_thumbnail',
]
