"""
Gallery API endpoints.

These APIs handle detection data, deletion, and GIF generation.
Note: Most gallery APIs are already in core/api/gallery.py
This file adds any page-specific endpoints.
"""
from flask import jsonify, request
from basebuddy.pages.gallery import gallery_bp

# Gallery APIs are registered in core/api/gallery.py
# This file is for any additional page-specific endpoints
