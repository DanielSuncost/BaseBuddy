"""
Date and time utilities.

Provides helper functions for date/time calculations including
sunrise/sunset approximations for daylight visualization.
"""
from datetime import datetime, timedelta
import math


def calculate_sunrise_sunset(date=None, latitude=40.0):
    """
    Calculate approximate sunrise and sunset times.
    
    Uses simplified astronomical calculation suitable for visualization.
    For precise times, consider using a library like astral or ephem.
    
    Args:
        date: Date to calculate for (defaults to today)
        latitude: Observer latitude in degrees (default 40.0)
        
    Returns:
        Tuple of (sunrise_datetime, sunset_datetime)
    """
    if date is None:
        date = datetime.now().date()
    
    # Day of year (1-365)
    day_of_year = date.timetuple().tm_yday
    
    # Solar declination (simplified approximation)
    declination = 23.45 * math.sin(math.radians(360 * (284 + day_of_year) / 365))
    
    # Hour angle at sunrise/sunset
    lat_rad = math.radians(latitude)
    decl_rad = math.radians(declination)
    
    try:
        cos_hour_angle = -math.tan(lat_rad) * math.tan(decl_rad)
        # Clamp to valid range for acos
        cos_hour_angle = max(-1.0, min(1.0, cos_hour_angle))
        hour_angle = math.degrees(math.acos(cos_hour_angle))
    except (ValueError, ZeroDivisionError):
        # Polar day/night - use reasonable defaults
        hour_angle = 90
    
    # Calculate sunrise and sunset hours (solar time)
    sunrise_hour = 12 - (hour_angle / 15)
    sunset_hour = 12 + (hour_angle / 15)
    
    # Clamp to reasonable bounds
    sunrise_hour = max(4, min(8, sunrise_hour))
    sunset_hour = max(16, min(22, sunset_hour))
    
    # Convert to datetime
    sunrise = datetime.combine(date, datetime.min.time()) + timedelta(hours=sunrise_hour)
    sunset = datetime.combine(date, datetime.min.time()) + timedelta(hours=sunset_hour)
    
    return sunrise, sunset


def format_duration(seconds):
    """
    Format duration in seconds as human-readable string.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted string (e.g., "2h 30m 15s")
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    
    hours = minutes // 60
    minutes = minutes % 60
    
    return f"{hours}h {minutes}m {seconds}s"


def parse_timestamp_filename(filename):
    """
    Parse timestamp from filename with format YYYYMMDD_HHMMSS.
    
    Args:
        filename: Filename containing timestamp
        
    Returns:
        datetime object or None if parsing fails
    """
    try:
        # Extract timestamp part (usually 15 chars: YYYYMMDD_HHMMSS)
        import re
        match = re.search(r'(\d{8}_\d{6})', filename)
        if match:
            timestamp_str = match.group(1)
            return datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
    except Exception:
        pass
    return None


