"""
Config page routes.
"""
from flask import render_template, request, redirect
from basebuddy.pages.config import config_bp


@config_bp.route('/config/setup')
def config_setup_page():
    """First-run wizard — cameras, Telegram, alert rules."""
    return render_template(
        'setup.html',
        active_page='config',
        active_config_tab='setup',
    )


@config_bp.route('/config', methods=['GET', 'POST'])
def config_page():
    """Main configuration page"""
    from basebuddy.modules.config import load_config_file, _config_txt_path
    from basebuddy.core.config_persist import upsert_config_exports
    from basebuddy.core.paths import get_repo_root
    
    success_message = ""
    
    if request.method == 'POST':
        try:
            new_config = {}
            
            # Camera URLs — only touch fields present in the form; an empty
            # field clears that camera, absent fields are left untouched
            for i in range(1, 21):
                field = f"cam{i}_url"
                if field in request.form:
                    new_config[f"CAM{i}"] = request.form.get(field, "").strip()
            
            # AI Settings
            ai_model = request.form.get("ai_model", "yolov8n.pt").strip()
            if ai_model:
                new_config["AI_MODEL"] = ai_model
            
            detection_enabled = request.form.get("detection_enabled", "false")
            new_config["DETECTION_ENABLED"] = "true" if detection_enabled == "on" else "false"
            
            ai_conf = request.form.get("ai_conf", "0.35").strip()
            try:
                ai_conf_val = float(ai_conf)
                if 0 <= ai_conf_val <= 1:
                    new_config["AI_CONF"] = str(ai_conf_val)
            except Exception:
                pass
            
            # Server settings
            host = request.form.get("host", "0.0.0.0").strip()
            if host:
                new_config["HOST"] = host
            
            port = request.form.get("port", "5000").strip()
            try:
                port_val = int(port)
                if 1 <= port_val <= 65535:
                    new_config["PORT"] = str(port_val)
            except Exception:
                pass
            
            # Recording settings
            seg_min = request.form.get("seg_minutes", "15").strip()
            try:
                seg_val = int(seg_min)
                if seg_val > 0:
                    new_config["SEG_MINUTES"] = str(seg_val)
            except Exception:
                pass
            
            ret_days = request.form.get("retention_days", "7").strip()
            try:
                ret_val = int(ret_days)
                if ret_val >= 0:
                    new_config["RETENTION_DAYS"] = str(ret_val)
            except Exception:
                pass
            
            # Merge into config.txt at the repo root — preserves all other settings
            upsert_config_exports(get_repo_root(), new_config)
            
            load_config_file()
            success_message = "Configuration saved successfully! Please restart the server for changes to take effect."
        except Exception as e:
            success_message = f"Error saving configuration: {str(e)}"
    
    # Load current configuration
    current_config = {}
    try:
        with open(_config_txt_path(), "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export ") and "=" in line:
                    parts = line[7:].split("=", 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip().strip('"')
                        current_config[key] = value
    except Exception:
        pass
    
    # Show at least 4 camera fields, plus any higher-numbered configured cameras
    cam_count = 4
    for i in range(1, 21):
        if current_config.get(f'CAM{i}'):
            cam_count = max(cam_count, i)

    return render_template('config.html',
                          active_page='config',
                          active_config_tab='settings',
                          config=current_config,
                          cam_count=cam_count,
                          success_message=success_message)


@config_bp.route('/thresholds')
def thresholds_redirect():
    return redirect("/config/thresholds", code=301)


@config_bp.route('/config/thresholds')
def thresholds_page():
    """Class threshold configuration page"""
    try:
        from basebuddy.modules.config import load_class_thresholds, AI_CONF, CAM_URLS
        from basebuddy.core.inference.types import COCO_CLASSES
        
        current_thresholds = load_class_thresholds()
        default_threshold = AI_CONF
        common_classes = list(COCO_CLASSES)
        
        # Build camera thresholds data for configured cameras
        configured = [i for i, url in enumerate(CAM_URLS) if url] or list(range(4))
        cameras_thresholds = []
        for cam_id in configured:
            camera_key = f"camera_{cam_id}"
            cam_thresholds = current_thresholds.get(camera_key, {})
            
            classes_data = []
            for class_name in common_classes:
                current_value = cam_thresholds.get(class_name, default_threshold)
                classes_data.append({
                    'name': class_name,
                    'value': current_value
                })
            
            cameras_thresholds.append({
                'id': cam_id,
                'classes': classes_data
            })
        
        return render_template('config_thresholds.html',
                              active_page='config',
                              active_config_tab='thresholds',
                              cameras=cameras_thresholds,
                              default_threshold=default_threshold)
    except Exception as e:
        return render_template('config_thresholds.html',
                              active_page='config',
                              active_config_tab='thresholds',
                              error=str(e))


@config_bp.route('/tracking-config')
def tracking_config_redirect():
    return redirect("/config/tracking", code=301)


@config_bp.route('/config/tracking')
def tracking_config_page():
    """Tracking configuration page"""
    from basebuddy.modules.config import CAM_URLS

    camera_ids = [i for i, url in enumerate(CAM_URLS) if url] or list(range(4))
    return render_template('config_tracking.html',
                          active_page='config',
                          active_config_tab='tracking',
                          camera_ids=camera_ids)


@config_bp.route('/disabled-classes')
def disabled_classes_redirect():
    return redirect("/config/disabled-classes", code=301)


@config_bp.route('/config/disabled-classes')
def disabled_classes_page():
    """Disabled classes management page"""
    try:
        from basebuddy.modules.config import reload_disabled_classes
        from basebuddy.core.inference.types import COCO_CLASSES
        
        available_classes = list(COCO_CLASSES)
        disabled_classes = set(reload_disabled_classes())
        
        classes_data = []
        for class_name in sorted(available_classes):
            classes_data.append({
                'name': class_name,
                'enabled': class_name not in disabled_classes
            })
        
        return render_template('config_disabled_classes.html',
                              active_page='config',
                              active_config_tab='disabled-classes',
                              classes=classes_data,
                              total_classes=len(available_classes),
                              disabled_count=len(disabled_classes))
    except Exception as e:
        return render_template('config_disabled_classes.html',
                              active_page='config',
                              active_config_tab='disabled-classes',
                              error=str(e))
