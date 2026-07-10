from flask import Blueprint, request, jsonify, render_template
import sqlite3
import logging
from datetime import datetime
from basebuddy.modules.recognition import FaceRecognizer
from basebuddy.modules.config import MEDIA_URL_PREFIX, MEDIA_BASE_DIR
import os

logger = logging.getLogger(__name__)

people_bp = Blueprint('people', __name__)


class _SharedDB:
    """Lazy proxy to the app-wide AnalyticsDB in shared state.

    Avoids creating a second AnalyticsDB (and re-running schema init) as an
    import side effect of this blueprint.
    """

    def _connect(self):
        import basebuddy.modules.state as shared_state
        return shared_state.analytics_db._connect()

    def __getattr__(self, name):
        import basebuddy.modules.state as shared_state
        return getattr(shared_state.analytics_db, name)


db = _SharedDB()

# Initialize recognizer for clustering operations
recognizer = None

def get_recognizer():
    global recognizer
    if recognizer is None:
        try:
            recognizer = FaceRecognizer()
        except Exception as e:
            logger.error("Failed to init recognizer in blueprint: %s", e)
    return recognizer

@people_bp.route('/people')
def people_ui():
    return render_template('people.html', active_page="people")

@people_bp.route('/api/people')
def api_get_people():
    with db._connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, thumbnail_path FROM people WHERE is_unknown=0 ORDER BY name")
        rows = cursor.fetchall()
        return jsonify([{'id': r[0], 'name': r[1], 'thumbnail_path': r[2]} for r in rows])

@people_bp.route('/api/people/clusters')
def api_get_clusters():
    # Clusters are people with is_unknown=1
    with db._connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, thumbnail_path FROM people WHERE is_unknown=1 ORDER BY id DESC")
        people = cursor.fetchall()
        
        clusters = []
        for pid, name, thumb in people:
            # Get count and sample images
            cursor.execute("SELECT count(*) FROM person_embeddings WHERE person_id=?", (pid,))
            count = cursor.fetchone()[0]
            
            if count == 0:
                continue # Skip empty clusters
                
            cursor.execute("SELECT image_path FROM person_embeddings WHERE person_id=? ORDER BY timestamp DESC LIMIT 4", (pid,))
            samples = [r[0] for r in cursor.fetchall()]
            
            clusters.append({
                'id': pid,
                'name': name,
                'count': count,
                'samples': samples
            })
            
        return jsonify(clusters)

@people_bp.route('/api/people/label', methods=['POST'])
def api_label_person():
    data = request.json
    pid = data.get('person_id')
    name = data.get('name')
        
    with db._connect() as conn:
        cursor = conn.cursor()
        # Check if name already exists
        cursor.execute("SELECT id FROM people WHERE name=? AND is_unknown=0", (name,))
        existing = cursor.fetchone()
        
        if existing:
            target_pid = existing[0]
            # Merge current cluster into existing person
            cursor.execute("UPDATE person_embeddings SET person_id=? WHERE person_id=?", (target_pid, pid))
            # Delete old cluster entry
            cursor.execute("DELETE FROM people WHERE id=?", (pid,))
        else:
            # Just update name and set unknown=0
            cursor.execute("UPDATE people SET name=?, is_unknown=0 WHERE id=?", (name, pid))
            
        conn.commit()
    
    # Reload recognizer gallery if active
    rec = get_recognizer()
    if rec:
        rec.reload_gallery()
        
    return jsonify({'ok': True})

@people_bp.route('/api/people/cluster', methods=['POST'])
def api_trigger_cluster():
    rec = get_recognizer()
    if rec:
        rec.cluster_unknowns()
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Recognition disabled'}), 500

@people_bp.route('/api/people/scan_history', methods=['POST'])
def api_scan_history():
    rec = get_recognizer()
    if rec:
        # Run in background ideally, but for now synchronous (might timeout on large DB)
        # Or we can just limit it
        try:
            rec.process_existing_events(days_lookback=30)
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': False, 'error': 'Recognition disabled'}), 500

@people_bp.route('/people/faces')
def face_gallery():
    """Face gallery page showing all detected faces from person detections"""
    return render_template('face_gallery.html', active_page="people")

@people_bp.route('/api/people/face_gallery')
def api_get_face_gallery():
    """Get paginated face gallery data"""
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        offset = (page - 1) * per_page

        with db._connect() as conn:
            cursor = conn.cursor()

            # Get total count
            cursor.execute('SELECT COUNT(*) FROM events WHERE class_name = ? AND full_image_path IS NOT NULL', ('person',))
            total_faces = cursor.fetchone()[0]

            # Get paginated face data
            # Note: person_embeddings doesn't have event_id, so we check by image_path matching
            cursor.execute('''
                SELECT e.id, e.camera_id, e.timestamp, e.confidence, e.full_image_path,
                       e.thumbnail_path,
                       CASE WHEN pe.id IS NOT NULL THEN 1 ELSE 0 END as has_embedding
                FROM events e
                LEFT JOIN person_embeddings pe ON e.full_image_path = pe.image_path OR e.thumbnail_path = pe.image_path
                WHERE e.class_name = ? AND e.full_image_path IS NOT NULL
                ORDER BY e.timestamp DESC
                LIMIT ? OFFSET ?
            ''', ('person', per_page, offset))

            faces = []
            for row in cursor.fetchall():
                event_id, camera_id, timestamp, confidence, full_image_path, thumbnail_path, has_embedding = row

                # Use thumbnail if available, otherwise full image
                display_url = thumbnail_path or full_image_path

                # Ensure URLs are properly formatted
                if display_url and not display_url.startswith('http') and not display_url.startswith('/'):
                    display_url = '/' + display_url.lstrip('/')
                if full_image_path and not full_image_path.startswith('http') and not full_image_path.startswith('/'):
                    full_image_path = '/' + full_image_path.lstrip('/')

                faces.append({
                    'id': event_id,
                    'camera_id': camera_id,
                    'timestamp': timestamp,
                    'confidence': float(confidence) if confidence else 0.0,
                    'image_url': full_image_path,
                    'thumbnail_url': display_url,
                    'has_embedding': bool(has_embedding)
                })

            total_pages = (total_faces + per_page - 1) // per_page if per_page > 0 else 0

            return jsonify({
                'faces': faces,
                'page': page,
                'per_page': per_page,
                'total_faces': total_faces,
                'total_pages': total_pages
            })
    except Exception as e:
        logger.exception("Face gallery API error")
        return jsonify({
            'error': str(e),
            'faces': [],
            'page': 1,
            'per_page': 50,
            'total_faces': 0,
            'total_pages': 0
        }), 500

@people_bp.route('/api/people/scan_faces', methods=['POST'])
def api_scan_faces():
    """Scan recent person detections for faces (only unprocessed ones) - runs in background"""
    import threading
    rec = get_recognizer()
    if not rec:
        return jsonify({'ok': False, 'error': 'Recognition disabled'}), 500

    # Check if already scanning
    status = rec.get_scanning_status()
    if status['active']:
        return jsonify({
            'ok': False,
            'error': 'Scan already in progress',
            'status': status
        }), 400

    def scan_thread():
        try:
            rec.process_existing_events(days_lookback=7, resume=True)
        except Exception as e:
            logger.error("Scan thread error: %s", e)

    # Start scanning in background thread
    thread = threading.Thread(target=scan_thread, daemon=True)
    thread.start()

    return jsonify({
        'ok': True,
        'message': 'Scan started in background',
        'status': rec.get_scanning_status()
    })

@people_bp.route('/api/people/scan_status', methods=['GET'])
def api_scan_status():
    """Get current scanning status"""
    rec = get_recognizer()
    if not rec:
        return jsonify({'active': False, 'error': 'Recognition disabled'}), 500
    
    return jsonify(rec.get_scanning_status())

@people_bp.route('/api/people/scan_pause', methods=['POST'])
def api_scan_pause():
    """Pause the current scanning operation"""
    rec = get_recognizer()
    if not rec:
        return jsonify({'ok': False, 'error': 'Recognition disabled'}), 500
    
    success = rec.pause_scanning()
    return jsonify({
        'ok': success,
        'status': rec.get_scanning_status()
    })

@people_bp.route('/api/people/scan_resume', methods=['POST'])
def api_scan_resume():
    """Resume a paused scanning operation"""
    rec = get_recognizer()
    if not rec:
        return jsonify({'ok': False, 'error': 'Recognition disabled'}), 500
    
    success = rec.resume_scanning()
    return jsonify({
        'ok': success,
        'status': rec.get_scanning_status()
    })

@people_bp.route('/api/people/scan_stop', methods=['POST'])
def api_scan_stop():
    """Stop the current scanning operation"""
    rec = get_recognizer()
    if not rec:
        return jsonify({'ok': False, 'error': 'Recognition disabled'}), 500
    
    success = rec.stop_scanning()
    return jsonify({
        'ok': success,
        'status': rec.get_scanning_status()
    })

@people_bp.route('/api/people/add_detection_to_library', methods=['POST'])
def api_add_detection_to_library():
    """Add a specific person detection to the face recognition library"""
    rec = get_recognizer()
    if not rec:
        return jsonify({'ok': False, 'error': 'Recognition disabled'}), 500

    try:
        data = request.json
        event_id = data.get('event_id')
        
        if not event_id:
            return jsonify({'ok': False, 'error': 'Missing event_id'}), 400

        # Get the detection from database
        with db._connect() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, camera_id, timestamp, class_name, full_image_path, thumbnail_path, confidence
                FROM events
                WHERE id = ? AND class_name = 'person'
            ''', (event_id,))
            
            row = cursor.fetchone()
            if not row:
                return jsonify({'ok': False, 'error': 'Detection not found or not a person detection'}), 404

            det_id, cam_id, ts_str, class_name, full_path, thumb_path, conf = row

            # Check if already processed
            image_path = full_path or thumb_path
            if image_path:
                cursor.execute('SELECT id FROM person_embeddings WHERE image_path = ?', (image_path,))
                if cursor.fetchone():
                    return jsonify({
                        'ok': True,
                        'message': 'Face already in library',
                        'already_exists': True
                    })

            # Load the image and process it
            import cv2
            from datetime import datetime
            
            # Parse timestamp
            try:
                if isinstance(ts_str, str):
                    if '.' in ts_str:
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
                    elif '+' in ts_str or 'Z' in ts_str:
                        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    else:
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                else:
                    ts = datetime.now()
            except ValueError:
                ts = datetime.now()

            # Load image from filesystem
            if image_path.startswith(MEDIA_URL_PREFIX):
                rel_path = image_path[len(MEDIA_URL_PREFIX):].lstrip('/')
                local_path = os.path.join(MEDIA_BASE_DIR, rel_path)
            else:
                local_path = image_path if os.path.isabs(image_path) else os.path.join(MEDIA_BASE_DIR, image_path.lstrip('/'))

            if not os.path.exists(local_path):
                return jsonify({'ok': False, 'error': 'Image file not found'}), 404

            img = cv2.imread(local_path)
            if img is None:
                return jsonify({'ok': False, 'error': 'Failed to load image'}), 500

            # Process with face recognizer
            person_id, conf_score, emb = rec.recognize_face(img)
            
            if emb is not None:
                # Register as unknown face (will be clustered later)
                rec.register_unknown_face(emb, img, cam_id, ts)
                return jsonify({
                    'ok': True,
                    'message': f'Face extracted and added to library (confidence: {conf_score:.2f})'
                })
            else:
                return jsonify({
                    'ok': False,
                    'error': 'No face detected in this image. Make sure it contains a clear face.'
                }), 400

    except Exception as e:
        logger.exception("Add detection to library error")
        return jsonify({'ok': False, 'error': str(e)}), 500

@people_bp.route('/api/people/delete_faces', methods=['POST'])
def api_delete_faces():
    """Delete selected faces from the gallery and their embeddings"""
    try:
        data = request.json
        face_ids = data.get('face_ids', [])

        if not face_ids:
            return jsonify({'ok': False, 'error': 'No face IDs provided'}), 400

        deleted_count = 0
        with db._connect() as conn:
            cursor = conn.cursor()

            for face_id in face_ids:
                # Get image paths for this event
                cursor.execute('SELECT full_image_path, thumbnail_path FROM events WHERE id = ?', (face_id,))
                row = cursor.fetchone()
                
                if row:
                    full_path, thumb_path = row
                    
                    # Delete embeddings associated with these image paths
                    if full_path:
                        cursor.execute('DELETE FROM person_embeddings WHERE image_path = ?', (full_path,))
                    if thumb_path:
                        cursor.execute('DELETE FROM person_embeddings WHERE image_path = ?', (thumb_path,))
                    
                    deleted_count += 1

            conn.commit()

        return jsonify({
            'ok': True,
            'deleted_count': deleted_count
        })

    except Exception as e:
        logger.exception("Delete faces error")
        return jsonify({'ok': False, 'error': str(e)}), 500

@people_bp.route('/api/people/delete_cluster', methods=['POST'])
def api_delete_cluster():
    """Delete a cluster and all its associated faces"""
    try:
        data = request.json
        cluster_id = data.get('cluster_id')

        if not cluster_id:
            return jsonify({'ok': False, 'error': 'No cluster ID provided'}), 400

        with db._connect() as conn:
            cursor = conn.cursor()

            # Count faces in cluster
            cursor.execute('SELECT COUNT(*) FROM person_embeddings WHERE person_id = ?', (cluster_id,))
            faces_count = cursor.fetchone()[0]

            # Delete all embeddings in this cluster
            cursor.execute('DELETE FROM person_embeddings WHERE person_id = ?', (cluster_id,))
            
            # Delete the cluster (person entry)
            cursor.execute('DELETE FROM people WHERE id = ?', (cluster_id,))

            conn.commit()

        return jsonify({
            'ok': True,
            'faces_deleted': faces_count
        })

    except Exception as e:
        logger.exception("Delete cluster error")
        return jsonify({'ok': False, 'error': str(e)}), 500

@people_bp.route('/api/people/cluster_by_similarity')
def api_cluster_by_similarity():
    """Cluster faces by similarity using embeddings"""
    try:
        threshold = float(request.args.get('threshold', 0.85))
        
        # Get all faces with embeddings
        with db._connect() as conn:
            cursor = conn.cursor()
            
            # Get all faces with embeddings
            # Use event_id if available, otherwise fall back to image path matching
            cursor.execute('''
                SELECT pe.id as embedding_id, pe.camera_id, pe.timestamp, pe.image_path, pe.confidence,
                       pe.embedding, pe.event_id,
                       COALESCE(e.full_image_path, pe.image_path) as full_image_path,
                       COALESCE(e.thumbnail_path, pe.image_path) as thumbnail_path
                FROM person_embeddings pe
                LEFT JOIN events e ON pe.event_id = e.id
                WHERE pe.embedding IS NOT NULL
                ORDER BY pe.timestamp DESC
            ''')
            
            rows = cursor.fetchall()
            
            if len(rows) < 2:
                return jsonify({
                    'clusters': [],
                    'message': f'Need at least 2 faces with embeddings to cluster. Found {len(rows)} face(s) with embeddings. Please scan for faces first using "Scan for New Faces" button.'
                })
            
            # Load embeddings
            import pickle
            import numpy as np
            from sklearn.cluster import DBSCAN
            from sklearn.metrics.pairwise import cosine_similarity
            
            faces_data = []
            embeddings = []
            
            for row in rows:
                emb_id, cam_id, ts, image_path, conf, emb_blob, event_id, full_path, thumb_path = row
                
                try:
                    emb = pickle.loads(emb_blob)
                    embeddings.append(emb)
                    
                    display_url = thumb_path or image_path or full_path
                    if display_url and not display_url.startswith('http') and not display_url.startswith('/'):
                        display_url = '/' + display_url.lstrip('/')
                    if full_path and not full_path.startswith('http') and not full_path.startswith('/'):
                        full_path = '/' + full_path.lstrip('/')
                    if not full_path:
                        full_path = image_path
                    
                    faces_data.append({
                        'id': event_id if event_id else emb_id,  # Use event_id if available, else embedding_id
                        'camera_id': cam_id,
                        'timestamp': ts,
                        'confidence': float(conf) if conf else 1.0,
                        'image_url': full_path or image_path,
                        'thumbnail_url': display_url,
                        'has_embedding': True,
                        'embedding_id': emb_id
                    })
                except Exception:
                    logger.exception("Error loading embedding %s", emb_id)
                    continue
            
            if len(embeddings) < 2:
                return jsonify({
                    'clusters': [],
                    'message': 'Not enough valid embeddings to cluster'
                })
            
            # Convert to numpy array
            X = np.array(embeddings)
            
            # Normalize embeddings for cosine similarity
            norms = np.linalg.norm(X, axis=1, keepdims=True)
            X_norm = X / (norms + 1e-8)
            
            # Compute pairwise cosine similarities
            similarity_matrix = cosine_similarity(X_norm)
            
            # Use DBSCAN with cosine distance (1 - similarity)
            # eps is the maximum distance (1 - threshold) for clustering
            eps = 1.0 - threshold
            
            # Convert similarity to distance
            distance_matrix = 1.0 - similarity_matrix
            
            # DBSCAN clustering
            clustering = DBSCAN(eps=eps, min_samples=2, metric='precomputed').fit(distance_matrix)
            labels = clustering.labels_
            
            # Group faces by cluster
            clusters = {}
            noise_faces = []
            
            for idx, label in enumerate(labels):
                if label == -1:
                    # Noise (not similar enough to any cluster)
                    noise_faces.append(faces_data[idx])
                else:
                    if label not in clusters:
                        clusters[label] = []
                    clusters[label].append(faces_data[idx])
            
            # Convert to list format
            cluster_list = []
            for label, faces in clusters.items():
                if len(faces) >= 2:  # Only include clusters with 2+ faces
                    cluster_list.append({
                        'cluster_id': label,
                        'faces': faces,
                        'size': len(faces)
                    })
            
            # Sort by cluster size (largest first)
            cluster_list.sort(key=lambda x: x['size'], reverse=True)
            
            return jsonify({
                'clusters': cluster_list,
                'noise_count': len(noise_faces),
                'total_clustered': sum(len(c['faces']) for c in cluster_list),
                'threshold': threshold
            })
            
    except Exception as e:
        logger.exception("Cluster by similarity error")
        return jsonify({'error': str(e)}), 500

@people_bp.route('/api/people/find_similar_faces')
def api_find_similar_faces():
    """Find faces similar to a specific face"""
    try:
        face_id = int(request.args.get('face_id'))
        threshold = float(request.args.get('threshold', 0.85))
        
        # Get the reference face embedding
        with db._connect() as conn:
            cursor = conn.cursor()
            
            # Get the reference face's embedding
            cursor.execute('''
                SELECT e.id, e.camera_id, e.timestamp, e.confidence, e.full_image_path,
                       e.thumbnail_path, pe.embedding, pe.id as embedding_id
                FROM events e
                INNER JOIN person_embeddings pe ON e.full_image_path = pe.image_path OR e.thumbnail_path = pe.image_path
                WHERE e.id = ? AND e.class_name = 'person'
            ''', (face_id,))
            
            ref_row = cursor.fetchone()
            if not ref_row:
                return jsonify({'error': 'Reference face not found or has no embedding'}), 404
            
            ref_event_id, ref_cam_id, ref_ts, ref_conf, ref_full_path, ref_thumb_path, ref_emb_blob, ref_emb_id = ref_row
            
            # Load reference embedding
            import pickle
            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity
            
            ref_emb = pickle.loads(ref_emb_blob)
            ref_emb_norm = ref_emb / (np.linalg.norm(ref_emb) + 1e-8)
            
            # Get all other faces with embeddings
            cursor.execute('''
                SELECT e.id, e.camera_id, e.timestamp, e.confidence, e.full_image_path,
                       e.thumbnail_path, pe.embedding, pe.id as embedding_id
                FROM events e
                INNER JOIN person_embeddings pe ON e.full_image_path = pe.image_path OR e.thumbnail_path = pe.image_path
                WHERE e.class_name = 'person' AND e.full_image_path IS NOT NULL AND e.id != ?
                ORDER BY e.timestamp DESC
            ''', (face_id,))
            
            rows = cursor.fetchall()
            
            similar_faces = []
            
            for row in rows:
                event_id, cam_id, ts, conf, full_path, thumb_path, emb_blob, emb_id = row
                
                try:
                    emb = pickle.loads(emb_blob)
                    emb_norm = emb / (np.linalg.norm(emb) + 1e-8)
                    
                    # Calculate cosine similarity
                    similarity = float(np.dot(ref_emb_norm, emb_norm))
                    
                    if similarity >= threshold:
                        display_url = thumb_path or full_path
                        if display_url and not display_url.startswith('http') and not display_url.startswith('/'):
                            display_url = '/' + display_url.lstrip('/')
                        if full_path and not full_path.startswith('http') and not full_path.startswith('/'):
                            full_path = '/' + full_path.lstrip('/')
                        
                        similar_faces.append({
                            'id': event_id,
                            'camera_id': cam_id,
                            'timestamp': ts,
                            'confidence': float(conf) if conf else 0.0,
                            'image_url': full_path,
                            'thumbnail_url': display_url,
                            'has_embedding': True,
                            'similarity': similarity
                        })
                except Exception as e:
                    logger.warning("Error processing face %s: %s", event_id, e)
                    continue
            
            # Sort by similarity (highest first)
            similar_faces.sort(key=lambda x: x['similarity'], reverse=True)
            
            return jsonify({
                'similar_faces': similar_faces,
                'reference_face_id': face_id,
                'threshold': threshold,
                'count': len(similar_faces)
            })
            
    except Exception as e:
        logger.exception("Find similar faces error")
        return jsonify({'error': str(e)}), 500

