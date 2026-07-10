"""
Gallery API endpoints.

Provides detection gallery functionality including timelines, grouping, and GIF generation.
"""
import logging
import os
import uuid

from flask import Blueprint, jsonify, request

from basebuddy.core.upload_safety import resolve_under_dir, safe_basename

logger = logging.getLogger(__name__)

gallery_api = Blueprint('gallery_api', __name__)


def _allowed_media_roots() -> list[str]:
    """Directories a client is permitted to read gallery images from."""
    from basebuddy.core.paths import abs_data_path, get_repo_root
    from basebuddy.modules.config import MEDIA_BASE_DIR, RECORD_ROOT

    repo = get_repo_root()
    roots = [
        abs_data_path(MEDIA_BASE_DIR) if MEDIA_BASE_DIR else os.path.join(repo, "media"),
        abs_data_path(RECORD_ROOT),
        os.path.join(repo, "stills"),
    ]
    return [r for r in roots if r]


def _resolve_gallery_image(client_path: str) -> str | None:
    """Map a client-supplied image reference to a safe path under a media root.

    Rejects absolute paths and ``..`` traversal that escape the allowed roots.
    """
    if not client_path:
        return None
    rel = client_path.lstrip("/")
    for prefix in ("media/", "recordings/", "stills/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    for root in _allowed_media_roots():
        resolved = resolve_under_dir(root, rel)
        if resolved and resolved.is_file():
            return str(resolved)
    return None


@gallery_api.route('/timeline/<int:detection_id>', methods=['GET'])
def get_detection_timeline(detection_id):
    """Similar detections for a reference id (used as timeline-style data in the UI)."""
    try:
        import basebuddy.modules.state as shared_state

        items = shared_state.analytics_db.get_similar_detections(detection_id)

        return jsonify({
            'ok': True,
            'data': items,
            'timeline': items,
        })

    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@gallery_api.route('/group/<path:group_key>', methods=['GET'])
def get_group_detections(group_key):
    """All detections in a track or position group (matches gallery modal grid)."""
    try:
        import basebuddy.modules.state as shared_state

        date_filter = request.args.get('date') or None
        hours = request.args.get('hours', type=int)
        if date_filter:
            hours = None
        elif hours is None:
            hours = 1

        cam_param = request.args.get('cam') or ''
        camera_ids = None
        if cam_param:
            ids = []
            for part in cam_param.split(','):
                try:
                    val = int(part.strip())
                    if val > 0:
                        ids.append(val - 1)
                except ValueError:
                    continue
            camera_ids = sorted(set(ids)) if ids else None

        detections = shared_state.analytics_db.get_detections_by_group_key(
            group_key,
            date_filter=date_filter,
            hours=hours,
            camera_ids=camera_ids,
        )

        return jsonify({
            'ok': True,
            'detections': detections,
            'ok': True,
            'data': detections,
            'count': len(detections),
        })

    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e),
            'detections': [],
        }), 500


@gallery_api.route('/generate_gif', methods=['POST'])
def generate_gif():
    """Generate GIF from detection image paths (accepts image_paths or images)."""
    try:
        data = request.get_json() or {}
        image_paths = data.get('image_paths') or data.get('images') or []
        output_name = safe_basename(data.get('name') or '') or f'det_{uuid.uuid4().hex[:12]}'
        fps = int(data.get('fps', 5))

        if not image_paths:
            return jsonify({
                'ok': False,
                'error': 'No images provided'
            }), 400

        from PIL import Image
        from basebuddy.modules.config import MEDIA_BASE_DIR

        # Generated GIFs are runtime data — store under the media root
        # (served by /media/<subpath>), not inside the application tree.
        output_dir = os.path.join(MEDIA_BASE_DIR, 'gifs')
        os.makedirs(output_dir, exist_ok=True)

        output_path = os.path.join(output_dir, f'{output_name}.gif')

        images = []
        for img_path in image_paths:
            resolved = _resolve_gallery_image(img_path)
            if resolved:
                im = Image.open(resolved)
                if im.mode != 'RGB':
                    im = im.convert('RGB')
                images.append(im)

        if images:
            images[0].save(
                output_path,
                save_all=True,
                append_images=images[1:],
                duration=max(1, int(1000 / max(fps, 1))),
                loop=0
            )

            gif_url = f'/media/gifs/{output_name}.gif'
            return jsonify({
                'ok': True,
                'gif_path': gif_url,
                'data': {
                    'gif_url': gif_url,
                    'path': output_path,
                },
            })

        return jsonify({
            'ok': False,
            'error': 'No valid images found on disk'
        }), 400

    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@gallery_api.route('/mark_false_positive', methods=['POST'])
def mark_false_positive():
    """Label one or more detections as false positives for training export."""
    try:
        import basebuddy.modules.state as shared_state

        data = request.get_json() or {}
        ids = list(data.get('ids') or [])
        if data.get('event_id') is not None:
            ids.insert(0, data['event_id'])
        ids = sorted({int(i) for i in ids if i is not None})
        if not ids:
            return jsonify({"ok": False, "error": "event_id or ids required"}), 400

        marked = shared_state.analytics_db.mark_detections_false_positive(ids)
        if marked == 0:
            return jsonify({"ok": False, "error": "No detections updated"}), 404
        return jsonify({"ok": True, "marked_count": marked})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@gallery_api.route('/events/<int:event_id>', methods=['GET'])
def get_event_detail(event_id):
    """Label state for a detection (gallery teach panel)."""
    try:
        import basebuddy.modules.state as shared_state

        ev = shared_state.analytics_db.get_event_by_id(event_id)
        if not ev:
            return jsonify({"ok": False, "error": "not found"}), 404
        person_name = None
        if ev.get("labeled_person_id"):
            with shared_state.analytics_db._connect() as conn:
                cur = conn.execute(
                    "SELECT name FROM people WHERE id = ?",
                    (int(ev["labeled_person_id"]),),
                )
                row = cur.fetchone()
                person_name = row[0] if row else None
        return jsonify({
            "ok": True,
            "event": ev,
            "person_name": person_name,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@gallery_api.route('/label', methods=['POST'])
def label_detection():
    """Add a training label to a detection (person name, corrected class, notes)."""
    try:
        import basebuddy.modules.state as shared_state

        data = request.get_json() or {}
        event_id = data.get('event_id')
        if event_id is None:
            return jsonify({"ok": False, "error": "event_id required"}), 400

        db = shared_state.analytics_db
        ev = db.get_event_by_id(int(event_id))
        if not ev:
            return jsonify({"ok": False, "error": "Detection not found"}), 404

        person_id = data.get('person_id')
        person_name = (data.get('person_name') or '').strip()
        if person_name and not person_id:
            person_id = db.get_or_create_named_person(person_name)

        corrected_class = (data.get('corrected_class') or '').strip() or None
        identity_label = (data.get('identity_label') or '').strip() or None
        notes = (data.get('notes') or data.get('user_label') or '').strip() or None
        training_label = data.get('training_label') or 'verified'
        add_to_face_library = bool(data.get('add_to_face_library'))

        if person_name and not identity_label:
            identity_label = person_name

        if not db.label_detection(
            int(event_id),
            training_label=training_label,
            user_label=notes,
            labeled_person_id=int(person_id) if person_id else None,
            corrected_class=corrected_class,
            identity_label=identity_label,
        ):
            return jsonify({"ok": False, "error": "Could not save label"}), 500

        face_added = False
        if add_to_face_library and ev.get('class_name') == 'person':
            try:
                from basebuddy.routes.people import get_recognizer
                from basebuddy.modules.config import MEDIA_BASE_DIR, MEDIA_URL_PREFIX
                import cv2
                from datetime import datetime

                rec = get_recognizer()
                if rec and rec.enabled:
                    image_path = ev.get('full_image_path') or ev.get('thumbnail_path')
                    if image_path and image_path.startswith(MEDIA_URL_PREFIX):
                        local_path = os.path.join(
                            MEDIA_BASE_DIR,
                            image_path[len(MEDIA_URL_PREFIX):].lstrip('/'),
                        )
                    else:
                        local_path = image_path

                    if local_path and os.path.isfile(local_path):
                        img = cv2.imread(local_path)
                        if img is not None:
                            ts = ev.get('timestamp')
                            if isinstance(ts, str):
                                try:
                                    ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                                except ValueError:
                                    ts = datetime.now()
                            else:
                                ts = datetime.now()
                            _pid, _conf, emb = rec.recognize_face(img, camera_id=ev.get('camera_id'))
                            if emb is not None:
                                emb_id = rec.register_unknown_face(
                                    emb, img, ev['camera_id'], ts, event_id=int(event_id)
                                )
                                if emb_id and person_id:
                                    with db._connect() as conn:
                                        conn.execute(
                                            "UPDATE person_embeddings SET person_id = ? WHERE id = ?",
                                            (int(person_id), int(emb_id)),
                                        )
                                        conn.commit()
                                face_added = True
            except Exception as exc:
                logger.warning("Face library add failed for event %s: %s", event_id, exc)

        return jsonify({
            "ok": True,
            "person_id": person_id,
            "face_added": face_added,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@gallery_api.route('/false_positive_zones', methods=['GET'])
def list_false_positive_zones():
    """List saved ignore zones (same camera + class + overlapping bbox → not stored)."""
    try:
        import basebuddy.modules.state as shared_state

        zones = shared_state.analytics_db.list_false_positive_zones(limit=500)
        return jsonify({"ok": True, "zones": zones})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@gallery_api.route('/false_positive_zones', methods=['POST'])
def create_false_positive_zone():
    """Create an ignore zone from a gallery event id (uses stored bbox + camera + class)."""
    try:
        import basebuddy.modules.state as shared_state

        data = request.get_json() or {}
        event_id = data.get("event_id")
        if event_id is None:
            return jsonify({"ok": False, "error": "event_id required"}), 400
        notes = data.get("notes")
        zid = shared_state.analytics_db.add_false_positive_zone_from_event(int(event_id), notes=notes)
        if zid is None:
            return jsonify({"ok": False, "error": "Event not found"}), 404
        return jsonify({"ok": True, "zone_id": zid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@gallery_api.route('/false_positive_zones/<int:zone_id>', methods=['DELETE'])
def delete_false_positive_zone(zone_id):
    try:
        import basebuddy.modules.state as shared_state

        if shared_state.analytics_db.delete_false_positive_zone(zone_id):
            return jsonify({"ok": True,})
        return jsonify({"ok": False, "error": "Zone not found"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@gallery_api.route('/export', methods=['GET'])
def export_training_data():
    """Export detections + false-positive zones for offline training (JSON or YOLO zip)."""
    import json
    import shutil
    import tempfile
    import zipfile
    from datetime import datetime
    from flask import send_file

    fmt = (request.args.get('format') or 'json').lower()
    hours = request.args.get('hours', 168, type=int)
    try:
        import basebuddy.modules.state as shared_state
        db = shared_state.analytics_db
        rows = db.get_events_for_export(hours=hours)
        zones = db.list_false_positive_zones(limit=5000)

        if fmt == 'json':
            false_positives = [r for r in rows if r.get('training_label') == 'false_positive']
            positives = [r for r in rows if r.get('training_label') != 'false_positive']
            person_labels = [r for r in rows if r.get('labeled_person_id')]
            payload = {
                'exported_at': datetime.utcnow().isoformat() + 'Z',
                'hours': hours,
                'detections': positives,
                'false_positives': false_positives,
                'person_labels': person_labels,
                'false_positive_zones': zones,
            }
            return jsonify({'ok': True, 'data': payload})

        if fmt == 'yolo':
            from basebuddy.modules.config import MEDIA_BASE_DIR, MEDIA_URL_PREFIX
            tmp = tempfile.mkdtemp(prefix='bb_export_')
            img_dir = os.path.join(tmp, 'images')
            neg_dir = os.path.join(tmp, 'negatives')
            lbl_dir = os.path.join(tmp, 'labels')
            os.makedirs(img_dir)
            os.makedirs(neg_dir)
            os.makedirs(lbl_dir)
            from basebuddy.core.inference.types import COCO_CLASSES
            coco_index = {name: i for i, name in enumerate(COCO_CLASSES)}

            copied = 0
            fp_copied = 0
            for row in rows:
                is_fp = row.get('training_label') == 'false_positive'
                src = row.get('full_image_path') or row.get('thumbnail_path')
                if src and src.startswith(MEDIA_URL_PREFIX):
                    src = os.path.join(MEDIA_BASE_DIR, src[len(MEDIA_URL_PREFIX):].lstrip('/'))
                elif src and src.startswith('/'):
                    src = None
                if not src or not os.path.isfile(src):
                    continue
                eid = row['id']
                ext = os.path.splitext(src)[1] or '.jpg'
                dest_dir = neg_dir if is_fp else img_dir
                dest = os.path.join(dest_dir, f'{eid}{ext}')
                shutil.copy2(src, dest)
                if is_fp:
                    fp_copied += 1
                    continue
                cls = row.get('class_name', 'object')
                cid = coco_index.get(cls, 0)
                x1, y1, x2, y2 = row['bbox_x1'], row['bbox_y1'], row['bbox_x2'], row['bbox_y2']
                try:
                    from PIL import Image
                    with Image.open(dest) as im:
                        w, h = im.size
                    if w > 0 and h > 0:
                        cx = ((x1 + x2) / 2) / w
                        cy = ((y1 + y2) / 2) / h
                        bw = (x2 - x1) / w
                        bh = (y2 - y1) / h
                        with open(os.path.join(lbl_dir, f'{eid}.txt'), 'w') as lf:
                            lf.write(f'{cid} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n')
                        copied += 1
                except Exception:
                    pass

            meta = {
                'exported_at': datetime.utcnow().isoformat() + 'Z',
                'images': copied,
                'negatives': fp_copied,
                'false_positive_zones': zones,
            }
            with open(os.path.join(tmp, 'manifest.json'), 'w') as mf:
                json.dump(meta, mf, indent=2)

            zip_path = os.path.join(tmp, 'basebuddy_training.zip')
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for folder in ('images', 'labels', 'negatives'):
                    base = os.path.join(tmp, folder)
                    if not os.path.isdir(base):
                        continue
                    for fn in os.listdir(base):
                        zf.write(os.path.join(base, fn), arcname=f'{folder}/{fn}')
                zf.write(os.path.join(tmp, 'manifest.json'), arcname='manifest.json')

            return send_file(
                zip_path,
                as_attachment=True,
                download_name=f'basebuddy_training_{datetime.utcnow().strftime("%Y%m%d")}.zip',
            )

        return jsonify({'ok': False, 'error': 'format must be json or yolo'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@gallery_api.route('/delete', methods=['POST'])
def delete_gallery_items():
    """Delete detection gallery items."""
    try:
        data = request.get_json()
        detection_ids = data.get('ids', [])
        
        if not detection_ids:
            return jsonify({
                'ok': False,
                'error': 'No detection IDs provided'
            }), 400
        
        import basebuddy.modules.state as shared_state
        
        deleted_count = 0
        for det_id in detection_ids:
            try:
                shared_state.analytics_db.delete_detection(det_id)
                deleted_count += 1
            except Exception:
                pass
        
        return jsonify({
            'ok': True,
            'data': {
                'deleted_count': deleted_count
            }
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


