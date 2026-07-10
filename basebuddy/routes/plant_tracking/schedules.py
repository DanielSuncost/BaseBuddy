"""Scheduled mask generation: schedule storage and CRUD endpoints."""

from flask import request, jsonify
import os
import json
from datetime import datetime

from . import plant_tracking_bp, logger


# ============ SCHEDULED MASK GENERATION ============

SCHEDULES_DIR = "mask_schedules"
os.makedirs(SCHEDULES_DIR, exist_ok=True)

def get_schedule_file(camera_id):
    """Get path to schedule config file for a camera"""
    return os.path.join(SCHEDULES_DIR, f"camera_{camera_id}_schedules.json")

def load_schedules(camera_id):
    """Load schedules for a camera"""
    schedule_file = get_schedule_file(camera_id)
    if os.path.exists(schedule_file):
        try:
            with open(schedule_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading schedules: {e}")
            return []
    return []

def save_schedules(camera_id, schedules):
    """Save schedules for a camera"""
    schedule_file = get_schedule_file(camera_id)
    try:
        with open(schedule_file, 'w') as f:
            json.dump(schedules, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving schedules: {e}")
        return False

@plant_tracking_bp.route('/api/plant-tracking/<int:camera_id>/schedules', methods=['GET'])
def api_get_schedules(camera_id):
    """Get all schedules for a camera"""
    try:
        schedules = load_schedules(camera_id)
        return jsonify({
            'ok': True,
            'schedules': schedules
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<int:camera_id>/schedules', methods=['POST'])
def api_create_schedule(camera_id):
    """Create a new mask generation schedule"""
    try:
        data = request.json
        
        # Validate required fields
        if 'interval' not in data:
            return jsonify({'ok': False, 'error': 'interval is required'}), 400
        
        # Load existing schedules
        schedules = load_schedules(camera_id)
        
        # Generate new schedule ID
        schedule_id = max([s.get('id', 0) for s in schedules], default=0) + 1
        
        # Create new schedule
        new_schedule = {
            'id': schedule_id,
            'camera_id': camera_id,
            'interval': data['interval'],
            'pattern_id': data.get('pattern_id', -1),
            'start_time': data.get('start_time', '00:00'),
            'method': data.get('method', 'union'),
            'enabled': data.get('enabled', True),
            'created_at': datetime.now().isoformat(),
            'last_run': None
        }
        
        schedules.append(new_schedule)
        
        # Save schedules
        if save_schedules(camera_id, schedules):
            return jsonify({
                'ok': True,
                'schedule': new_schedule
            })
        else:
            return jsonify({'ok': False, 'error': 'Failed to save schedule'}), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<int:camera_id>/schedules/<int:schedule_id>', methods=['PATCH'])
def api_update_schedule(camera_id, schedule_id):
    """Update an existing schedule (e.g., enable/disable)"""
    try:
        data = request.json
        schedules = load_schedules(camera_id)
        
        # Find and update the schedule
        for schedule in schedules:
            if schedule['id'] == schedule_id:
                # Update fields
                if 'enabled' in data:
                    schedule['enabled'] = data['enabled']
                if 'interval' in data:
                    schedule['interval'] = data['interval']
                if 'pattern_id' in data:
                    schedule['pattern_id'] = data['pattern_id']
                if 'method' in data:
                    schedule['method'] = data['method']
                if 'start_time' in data:
                    schedule['start_time'] = data['start_time']
                
                schedule['updated_at'] = datetime.now().isoformat()
                
                # Save schedules
                if save_schedules(camera_id, schedules):
                    return jsonify({
                        'ok': True,
                        'schedule': schedule
                    })
                else:
                    return jsonify({'ok': False, 'error': 'Failed to save schedule'}), 500
        
        return jsonify({'ok': False, 'error': 'Schedule not found'}), 404
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<int:camera_id>/schedules/<int:schedule_id>', methods=['DELETE'])
def api_delete_schedule(camera_id, schedule_id):
    """Delete a schedule"""
    try:
        schedules = load_schedules(camera_id)
        
        # Filter out the schedule to delete
        schedules = [s for s in schedules if s['id'] != schedule_id]
        
        # Save updated schedules
        if save_schedules(camera_id, schedules):
            return jsonify({'ok': True})
        else:
            return jsonify({'ok': False, 'error': 'Failed to save schedules'}), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500
