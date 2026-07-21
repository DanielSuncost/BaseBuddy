"""SAM + color-based segmentation helper functions."""

import os
import cv2
import numpy as np

from . import logger
from .helpers import get_sam_predictor

# Helper functions

def analyze_color_profile(image, mask):
    """Analyze color distribution within a mask region"""
    if mask is None or np.sum(mask) == 0:
        return None
    
    # Convert to HSV for better color analysis
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # Get pixels within mask
    masked_hsv = hsv[mask > 0]
    masked_bgr = image[mask > 0]
    
    if len(masked_hsv) == 0:
        return None
    
    # Calculate color statistics
    h_mean, s_mean, v_mean = np.mean(masked_hsv, axis=0)
    h_std, s_std, v_std = np.std(masked_hsv, axis=0)
    
    # Calculate reasonable ranges (mean ± 2*std, clamped)
    # For hue, be a bit more permissive to handle lighting variations
    h_min = max(0, int(h_mean - 2.5 * h_std))
    h_max = min(180, int(h_mean + 2.5 * h_std))
    s_min = max(0, int(s_mean - 2 * s_std))
    s_max = min(255, int(s_mean + 2 * s_std))
    v_min = max(0, int(v_mean - 2 * v_std))
    v_max = min(255, int(v_mean + 2 * v_std))
    
    # Get representative colors (percentiles for robustness)
    percentiles = [10, 50, 90]
    dominant_colors = []
    for p in percentiles:
        b, g, r = np.percentile(masked_bgr, p, axis=0)
        dominant_colors.append([int(r), int(g), int(b)])  # Convert to RGB
    
    return {
        'hsv_range': {
            'h_min': h_min,
            'h_max': h_max,
            's_min': s_min,
            's_max': s_max,
            'v_min': v_min,
            'v_max': v_max
        },
        'hsv_mean': {
            'h': int(h_mean),
            's': int(s_mean),
            'v': int(v_mean)
        },
        'dominant_colors_rgb': dominant_colors
    }

def apply_color_filter(image, color_profile):
    """Apply color-based segmentation"""
    if color_profile is None:
        return np.zeros(image.shape[:2], dtype=np.uint8)
    
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hsv_range = color_profile['hsv_range']
    
    # Create mask from HSV range
    lower = np.array([hsv_range['h_min'], hsv_range['s_min'], hsv_range['v_min']])
    upper = np.array([hsv_range['h_max'], hsv_range['s_max'], hsv_range['v_max']])
    
    color_mask = cv2.inRange(hsv, lower, upper)
    
    # Clean up with morphological operations
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, kernel)
    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN, kernel)
    
    return color_mask

def analyze_prompt_pattern(points, labels, image_shape, image_path=None):
    """Analyze spatial pattern of prompts and extract color profile"""
    h, w = image_shape[:2] if len(image_shape) >= 2 else (1080, 1920)
    
    fg_points = [p for p, l in zip(points, labels) if l == 1]
    bg_points = [p for p, l in zip(points, labels) if l == 0]
    
    if not fg_points:
        return {}
    
    fg_array = np.array(fg_points)
    min_x, min_y = fg_array.min(axis=0)
    max_x, max_y = fg_array.max(axis=0)
    center_x, center_y = fg_array.mean(axis=0)
    
    pattern = {
        'bbox': {
            'min_x': float(min_x / w),
            'max_x': float(max_x / w),
            'min_y': float(min_y / h),
            'max_y': float(max_y / h)
        },
        'center': {
            'x': float(center_x / w),
            'y': float(center_y / h)
        },
        'fg_points_relative': [[float(x/w), float(y/h)] for x, y in fg_points],
        'bg_points_relative': [[float(x/w), float(y/h)] for x, y in bg_points]
    }
    
    # Extract color profile if image path provided
    if image_path and os.path.exists(image_path):
        try:
            image = cv2.imread(image_path)
            if image is not None:
                # Generate a quick SAM mask to analyze colors
                predictor = get_sam_predictor()
                if predictor is not None:
                    predictor.set_image(image)
                    point_coords = np.array([[int(x), int(y)] for x, y in fg_points + bg_points])
                    point_labels = np.array([1] * len(fg_points) + [0] * len(bg_points))
                    masks, _, _ = predictor.predict(
                        point_coords=point_coords,
                        point_labels=point_labels,
                        multimask_output=False
                    )
                    mask = (masks[0] * 255).astype(np.uint8)
                    
                    color_profile = analyze_color_profile(image, mask)
                    if color_profile:
                        pattern['color_profile'] = color_profile
        except Exception as e:
            logger.info(f"Could not extract color profile: {e}")
    
    return pattern

def apply_pattern(pattern, image_shape):
    """Generate prompts from saved pattern"""
    h, w = image_shape[:2]
    points = []
    labels = []
    
    for rel_x, rel_y in pattern.get('fg_points_relative', []):
        points.append([int(rel_x * w), int(rel_y * h)])
        labels.append(1)
    
    for rel_x, rel_y in pattern.get('bg_points_relative', []):
        points.append([int(rel_x * w), int(rel_y * h)])
        labels.append(0)
    
    return points, labels

def segment_with_prompts(predictor, image, points, labels, color_profile=None, hybrid_mode='union'):
    """Run hybrid SAM + color-based segmentation
    
    Args:
        predictor: SAM predictor
        image: Input image
        points: Annotation points
        labels: Point labels (1=foreground, 0=background)
        color_profile: Optional color profile for hybrid segmentation
        hybrid_mode: How to combine masks - 'union', 'intersection', 'sam_only', 'color_only'
    
    Returns:
        Combined mask (uint8, 0-255)
    """
    if not points:
        return np.zeros(image.shape[:2], dtype=np.uint8)
    
    # Get SAM mask
    predictor.set_image(image)
    point_coords = np.array(points)
    point_labels = np.array(labels)
    
    masks, scores, logits = predictor.predict(
        point_coords=point_coords,
        point_labels=point_labels,
        multimask_output=False
    )
    
    sam_mask = (masks[0] * 255).astype(np.uint8)
    
    # If no color profile or sam_only mode, return SAM mask
    if color_profile is None or hybrid_mode == 'sam_only':
        return sam_mask
    
    # Get color-based mask
    color_mask = apply_color_filter(image, color_profile)
    
    # If color_only mode, return color mask
    if hybrid_mode == 'color_only':
        return color_mask
    
    # Combine masks based on mode
    if hybrid_mode == 'intersection':
        # Both methods must agree (more conservative)
        combined = cv2.bitwise_and(sam_mask, color_mask)
    else:  # 'union' (default)
        # Either method works (more inclusive, good for capturing full plant)
        combined = cv2.bitwise_or(sam_mask, color_mask)
        
        # Refine: keep only regions that have significant SAM support
        # This prevents color bleeding into pure background
        sam_dilated = cv2.dilate(sam_mask, np.ones((15, 15), np.uint8))
        combined = cv2.bitwise_and(combined, sam_dilated)
    
    # Final cleanup
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)
    combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel)
    
    return combined
