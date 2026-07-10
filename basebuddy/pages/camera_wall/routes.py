"""
Camera Wall page routes.
"""
from flask import render_template
from basebuddy.pages.camera_wall import camera_wall_bp


@camera_wall_bp.route('/')
def index():
    """Main camera wall page"""
    return render_template('camera_wall.html', active_page='dashboard', container_class='container-fluid')
