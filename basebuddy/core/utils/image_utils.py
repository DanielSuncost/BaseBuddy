"""
Image processing utilities.

Helper functions for drawing overlays, progress indicators,
clock faces, and other visual elements on images.
"""
import cv2
import numpy as np
from datetime import datetime
import math


def draw_progress_meter(img, progress, x, y, radius=25):
    """
    Draw a circular progress meter on an image.
    
    Args:
        img: Image array (modified in place)
        progress: Progress value 0.0 to 1.0
        x: X coordinate of center
        y: Y coordinate of center
        radius: Radius of the circle (default 25)
    """
    # Background circle
    cv2.circle(img, (x, y), radius, (40, 40, 40), -1)
    cv2.circle(img, (x, y), radius, (80, 80, 80), 2)
    
    # Progress arc
    if progress > 0:
        angle = int(360 * progress)
        # Draw filled arc by drawing pie slices
        pts = []
        pts.append((x, y))
        for i in range(angle + 1):
            angle_rad = math.radians(i - 90)  # Start from top
            px = int(x + radius * math.cos(angle_rad))
            py = int(y + radius * math.sin(angle_rad))
            pts.append((px, py))
        
        if len(pts) > 2:
            pts = np.array(pts, dtype=np.int32)
            cv2.fillPoly(img, [pts], (0, 200, 100))
    
    # Border
    cv2.circle(img, (x, y), radius, (200, 200, 200), 2)
    
    # Progress text
    progress_text = f"{int(progress * 100)}%"
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.4
    font_thickness = 1
    text_size = cv2.getTextSize(progress_text, font, font_scale, font_thickness)[0]
    text_x = x - text_size[0] // 2
    text_y = y + text_size[1] // 2
    cv2.putText(img, progress_text, (text_x, text_y), font, 
                font_scale, (255, 255, 255), font_thickness)


def draw_clock_face(img, timestamp_str, x, y, radius=25):
    """
    Draw a clock face showing the time from a timestamp.
    
    Args:
        img: Image array (modified in place)
        timestamp_str: Timestamp string (format: YYYYMMDD_HHMMSS)
        x: X coordinate of center
        y: Y coordinate of center
        radius: Radius of the clock (default 25)
    """
    try:
        dt = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
    except Exception:
        return
    
    # Background
    cv2.circle(img, (x, y), radius, (40, 40, 40), -1)
    cv2.circle(img, (x, y), radius, (200, 200, 200), 2)
    
    # Hour markers
    for i in range(12):
        angle = math.radians(i * 30 - 90)
        x1 = int(x + (radius - 5) * math.cos(angle))
        y1 = int(y + (radius - 5) * math.sin(angle))
        x2 = int(x + (radius - 2) * math.cos(angle))
        y2 = int(y + (radius - 2) * math.sin(angle))
        cv2.line(img, (x1, y1), (x2, y2), (150, 150, 150), 1)
    
    # Hour hand
    hour_angle = math.radians((dt.hour % 12) * 30 + dt.minute * 0.5 - 90)
    hour_x = int(x + (radius * 0.5) * math.cos(hour_angle))
    hour_y = int(y + (radius * 0.5) * math.sin(hour_angle))
    cv2.line(img, (x, y), (hour_x, hour_y), (255, 255, 255), 2)
    
    # Minute hand
    minute_angle = math.radians(dt.minute * 6 - 90)
    minute_x = int(x + (radius * 0.7) * math.cos(minute_angle))
    minute_y = int(y + (radius * 0.7) * math.sin(minute_angle))
    cv2.line(img, (x, y), (minute_x, minute_y), (200, 200, 200), 2)
    
    # Center dot
    cv2.circle(img, (x, y), 3, (255, 255, 255), -1)


def draw_text_with_background(img, text, position, font=cv2.FONT_HERSHEY_SIMPLEX,
                              font_scale=0.6, font_thickness=1, text_color=(255, 255, 255),
                              bg_color=(0, 0, 0), padding=5):
    """
    Draw text with a background rectangle for better visibility.
    
    Args:
        img: Image array (modified in place)
        text: Text to draw
        position: (x, y) tuple for bottom-left corner of text
        font: OpenCV font (default FONT_HERSHEY_SIMPLEX)
        font_scale: Font scale (default 0.6)
        font_thickness: Font thickness (default 1)
        text_color: RGB tuple for text color
        bg_color: RGB tuple for background color
        padding: Padding around text (default 5)
    """
    x, y = position
    text_size = cv2.getTextSize(text, font, font_scale, font_thickness)[0]
    
    # Background rectangle
    cv2.rectangle(img,
                 (x - padding, y - text_size[1] - padding),
                 (x + text_size[0] + padding, y + padding),
                 bg_color, -1)
    
    # Text
    cv2.putText(img, text, (x, y), font, font_scale, text_color, font_thickness)


def create_thumbnail(image, max_size=(320, 240)):
    """
    Create a thumbnail of an image while maintaining aspect ratio.
    
    Args:
        image: Source image array
        max_size: Maximum (width, height) tuple
        
    Returns:
        Resized image array
    """
    h, w = image.shape[:2]
    max_w, max_h = max_size
    
    # Calculate scaling factor
    scale = min(max_w / w, max_h / h)
    
    if scale >= 1:
        return image
    
    new_w = int(w * scale)
    new_h = int(h * scale)
    
    return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)


