"""
Gallery page module.

Browse detection snapshots and manage saved images.
"""
from flask import Blueprint

# Create blueprint
gallery_bp = Blueprint('gallery', __name__)

# Import routes and API to register them with the blueprint
from basebuddy.pages.gallery import routes
from basebuddy.pages.gallery import api
