"""
Power Management UI Routes
"""
import logging
from datetime import time as dt_time

from flask import Blueprint, jsonify, redirect, render_template, request

from basebuddy.modules.power_management import PowerProfile, get_power_manager

logger = logging.getLogger(__name__)

power_bp = Blueprint('power', __name__)


@power_bp.route('/power')
def power_redirect():
    return redirect("/config/power", code=301)


@power_bp.route('/config/power')
def power_ui():
    return render_template(
        'config_power.html',
        active_page='config',
        active_config_tab='power',
    )


@power_bp.route('/api/power/status')
def api_power_status():
    try:
        pm = get_power_manager()
        status = pm.get_status()
        profiles = {
            'maximum': pm.profiles[PowerProfile.MAXIMUM],
            'high': pm.profiles[PowerProfile.HIGH],
            'medium': pm.profiles[PowerProfile.MEDIUM],
            'low': pm.profiles[PowerProfile.LOW],
            'minimum': pm.profiles[PowerProfile.MINIMUM],
        }
        time_based_profile = pm.get_time_based_profile()
        manual_override = pm.current_profile != time_based_profile
        return jsonify({
            'current_profile': status['current_profile'],
            'is_night_time': status['is_night_time'],
            'detection_rate': status['detection_rate'],
            'frame_skip_rate': status['settings']['frame_skip_rate'],
            'gpu_enabled': status['gpu_enabled'],
            'manual_override': manual_override,
            'time_based_profile': time_based_profile.value,
            'profiles': profiles,
        })
    except Exception as e:
        logger.exception("power API request failed")
        return jsonify({'error': str(e)}), 500


@power_bp.route('/api/power/profile', methods=['POST'])
def api_set_profile():
    try:
        data = request.json or {}
        profile_name = data.get('profile', 'auto')
        pm = get_power_manager()
        if profile_name == 'auto':
            pm.current_profile = None
            return jsonify({'ok': True, 'message': 'Reset to automatic profile'})
        profile_map = {
            'maximum': PowerProfile.MAXIMUM,
            'high': PowerProfile.HIGH,
            'medium': PowerProfile.MEDIUM,
            'low': PowerProfile.LOW,
            'minimum': PowerProfile.MINIMUM,
        }
        if profile_name not in profile_map:
            return jsonify({'ok': False, 'error': f'Invalid profile: {profile_name}'}), 400
        pm.update_profile(profile_map[profile_name])
        return jsonify({'ok': True, 'message': f'Profile set to {profile_name}'})
    except Exception as e:
        logger.exception("power API request failed")
        return jsonify({'ok': False, 'error': str(e)}), 500


@power_bp.route('/api/power/night-hours', methods=['POST'])
def api_set_night_hours():
    try:
        data = request.json or {}
        start_str = data.get('start', '22:00')
        end_str = data.get('end', '06:00')
        start_hour, start_min = map(int, start_str.split(':'))
        end_hour, end_min = map(int, end_str.split(':'))
        pm = get_power_manager()
        pm.night_start = dt_time(start_hour, start_min)
        pm.night_end = dt_time(end_hour, end_min)
        return jsonify({
            'ok': True,
            'message': f'Night hours set to {start_str} - {end_str}',
            'night_start': start_str,
            'night_end': end_str,
        })
    except Exception as e:
        logger.exception("power API request failed")
        return jsonify({'ok': False, 'error': str(e)}), 500
