"""
Recordings page module.

View and manage recorded video clips.
"""
from flask import Blueprint

# Create blueprint
recordings_bp = Blueprint('recordings', __name__)

# Import routes and API to register them with the blueprint
from basebuddy.pages.recordings import routes
from basebuddy.pages.recordings import api
