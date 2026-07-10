import logging

logger = logging.getLogger(__name__)

import os
import time
import numpy as np
import cv2
import pickle
import threading
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

# Lazy import to avoid slow startup if not enabled
DEEPFACE_AVAILABLE = False
INSIGHTFACE_AVAILABLE = False

# Try DeepFace first (preferred)
# Note: If CuDNN version mismatch occurs, we'll handle it in __init__
try:
    from deepface import DeepFace
    # Note: We don't need to import functions - DeepFace.represent() is sufficient
    DEEPFACE_AVAILABLE = True
    logger.info("DeepFace available (primary backend)")
except ImportError as e:
    logger.warning(f"DeepFace not available: {e}")
    logger.info(f"   Error details: {type(e).__name__}: {str(e)}")
    # Check if it's a missing dependency
    if 'tf_keras' in str(e).lower() or 'tensorflow' in str(e).lower():
        logger.info("Try: pip install tf-keras")
except Exception as e:
    logger.warning(f"DeepFace import error: {e}")
    logger.info(f"   Error details: {type(e).__name__}: {str(e)}")
    # Check for CuDNN issues
    if 'cudnn' in str(e).lower() or 'dnn' in str(e).lower():
        logger.info("CuDNN version mismatch detected. DeepFace will use CPU mode.")
        # Set environment variable for CPU mode (will be used when TensorFlow initializes)
        os.environ.setdefault('CUDA_VISIBLE_DEVICES', '-1')

# Fallback to InsightFace only if DeepFace failed
if not DEEPFACE_AVAILABLE:
    try:
        import insightface
        from insightface.app import FaceAnalysis
        INSIGHTFACE_AVAILABLE = True
        logger.warning("Using InsightFace as fallback")
    except ImportError as e2:
        logger.error(f"InsightFace also not available: {e2}")
        logger.info("Recognition features disabled.")

try:
    from sklearn.cluster import DBSCAN
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("sklearn not available - clustering disabled")

from .database import AnalyticsDB
from .config import MEDIA_BASE_DIR, MEDIA_URL_PREFIX

class FaceRecognizer:
    def __init__(self, db_path: str = None):
        self.db = AnalyticsDB(db_path) if db_path else AnalyticsDB()
        self.app = None  # For InsightFace fallback
        self.gallery = [] # List of (person_id, embedding_vector)
        self.gallery_names = {} # Map person_id -> name
        self.sim_threshold = 0.5  # Cosine similarity threshold
        self.gpu_failed = False  # Track if GPU operations have failed
        # Scanning state management
        self._scanning_lock = threading.Lock()
        self._scanning_active = False
        self._scanning_paused = False
        self._scanning_thread = None
        self._scanning_progress = {'processed': 0, 'total': 0, 'faces_found': 0}
        # Check DeepFace availability at runtime (not just import time)
        self.use_deepface = DEEPFACE_AVAILABLE
        if not self.use_deepface:
            # Try importing DeepFace again at runtime in case it was installed after module load
            try:
                from deepface import DeepFace
                self.use_deepface = True
                logger.info("DeepFace found at runtime (wasn't available at import time)")
            except Exception:
                pass
        
        self.enabled = self.use_deepface or INSIGHTFACE_AVAILABLE

        if self.enabled:
            if self.use_deepface:
                # Use DeepFace with GPU acceleration - DO NOT initialize InsightFace
                logger.info("Initializing DeepFace with GPU acceleration...")
                logger.info("   Using DeepFace as primary backend (InsightFace will NOT be initialized)")
                # DeepFace will automatically use GPU if available via TensorFlow
                # Use RetinaFace detector and Facenet512 model for best results
                try:
                    import os
                    
                    # Configure TensorFlow GPU memory growth BEFORE importing TensorFlow
                    # This prevents TensorFlow from allocating all GPU memory at once
                    import tensorflow as tf
                    try:
                        # Enable memory growth to prevent TensorFlow from allocating all GPU memory
                        physical_devices = tf.config.list_physical_devices('GPU')
                        if physical_devices:
                            for device in physical_devices:
                                tf.config.experimental.set_memory_growth(device, True)
                            logger.info("TensorFlow GPU memory growth enabled")
                    except Exception as mem_err:
                        logger.info(f"Could not configure TensorFlow memory growth: {mem_err}")
                    
                    # Configure XLA to find libdevice files BEFORE importing TensorFlow
                    # Point XLA to libdevice files in venv (from triton package)
                    # Get project root (two dirnames up from modules/recognition.py)
                    from basebuddy.core.paths import get_repo_root
                    project_root = get_repo_root()
                    libdevice_dir = os.path.join(project_root, 'venv', 'lib', 'python3.12', 'site-packages', 'triton', 'backends', 'nvidia', 'lib')
                    libdevice_file = os.path.join(libdevice_dir, 'libdevice.10.bc')
                    
                    libdevice_dir_abs = os.path.abspath(libdevice_dir)
                    if os.path.exists(libdevice_file):
                        # Set XLA to use the libdevice directory (absolute path)
                        # Use os.environ[] to override any previous settings
                        os.environ['XLA_FLAGS'] = f'--xla_gpu_cuda_data_dir={libdevice_dir_abs} --xla_gpu_force_compilation_parallelism=1'
                        # Ensure XLA is enabled (don't disable it)
                        os.environ['TF_XLA_FLAGS'] = ''
                        
                        # Also create a symlink in current directory for TensorFlow's fallback search
                        # TensorFlow sometimes looks for ./libdevice.10.bc in the working directory
                        cwd_libdevice = os.path.join(os.getcwd(), 'libdevice.10.bc')
                        if not os.path.exists(cwd_libdevice):
                            try:
                                os.symlink(os.path.abspath(libdevice_file), cwd_libdevice)
                                logger.info(f"Created symlink: {cwd_libdevice} -> {libdevice_file}")
                            except Exception as sym_err:
                                logger.info(f"Could not create symlink: {sym_err}")
                        
                        logger.info(f"Configured XLA libdevice path: {libdevice_dir_abs}")
                    else:
                        # Fallback: disable XLA if libdevice not found
                        os.environ['TF_XLA_FLAGS'] = '--tf_xla_enable_xla_devices=false'
                        os.environ['XLA_FLAGS'] = '--xla_gpu_force_compilation_parallelism=1'
                        logger.info(f"libdevice not found at: {libdevice_file}")
                        logger.info(f"   Checked path: {libdevice_dir_abs}")
                        logger.info("   XLA disabled, GPU will work without JIT compilation")
                    
                    # Ensure PyTorch CUDA context is initialized first to avoid conflicts
                    try:
                        import torch
                        if torch.cuda.is_available():
                            # Initialize PyTorch CUDA context if not already done
                            _ = torch.zeros(1).cuda()
                    except Exception:
                        pass
                    
                    # Now import TensorFlow AFTER setting environment variables
                    import tensorflow as tf
                    
                    # Enable JIT compilation for GPU acceleration
                    # Now that libdevice path is configured, XLA/JIT should work
                    try:
                        # Don't disable JIT - we want GPU acceleration with XLA
                        # tf.config.optimizer.set_jit(False)  # Commented out to enable GPU JIT
                        pass
                    except Exception:
                        pass
                    
                    # Set TensorFlow to use CPU only if GPU fails (will be set later if needed)
                    # For now, try GPU but configure it properly
                    
                    # Check for CuDNN version mismatch - if detected, force CPU
                    # This prevents GPU errors when CuDNN versions don't match
                    try:
                        gpus = tf.config.list_physical_devices('GPU')
                        if gpus:
                            # Try to configure GPU, but catch CuDNN errors
                            try:
                                # Set memory growth BEFORE any operations
                                for gpu in gpus:
                                    tf.config.experimental.set_memory_growth(gpu, True)
                                
                                # Initialize TensorFlow GPU context properly
                                # Create a small operation to initialize the context
                                with tf.device('/GPU:0'):
                                    # Use a simple operation that doesn't require CuDNN or XLA
                                    test = tf.constant([1.0], dtype=tf.float32)
                                    _ = tf.identity(test)  # Force execution
                                
                                logger.info(f"GPU devices available and working: {[d.name for d in gpus]}")
                            except Exception as gpu_err:
                                # GPU/CuDNN issue - check if it's a context error
                                if 'CUDA_ERROR_NOT_INITIALIZED' in str(gpu_err) or 'Failed setting context' in str(gpu_err):
                                    logger.warning(f"CUDA context initialization issue: {gpu_err}")
                                    logger.info("   This may be due to PyTorch/TensorFlow CUDA context conflict")
                                    logger.info("   Retrying with explicit context reset...")
                                    try:
                                        # Try to reset CUDA context
                                        import torch
                                        if torch.cuda.is_available():
                                            torch.cuda.empty_cache()
                                            torch.cuda.synchronize()
                                        # Retry TensorFlow GPU init
                                        with tf.device('/GPU:0'):
                                            test = tf.constant([1.0])
                                        logger.info("GPU initialization successful after retry")
                                    except Exception as retry_err:
                                        logger.warning(f"GPU retry failed: {retry_err}")
                                        logger.info("   Forcing CPU mode for DeepFace")
                                        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
                                else:
                                    # GPU/CuDNN/XLA issue - check for libdevice error
                                    error_str = str(gpu_err).lower()
                                    if 'libdevice' in error_str or 'jit compilation' in error_str or 'xla' in error_str:
                                        logger.warning(f"TensorFlow XLA/JIT compilation error detected: {gpu_err}")
                                        logger.info("   This is often due to missing CUDA libdevice files")
                                        logger.info("   Disabling XLA/JIT and retrying GPU mode...")
                                        try:
                                            # Try to fix libdevice path and retry
                                            logger.info("   Attempting to fix libdevice path...")
                                            # The libdevice path should already be set from initialization
                                            # Retry GPU init - XLA should now find libdevice
                                            with tf.device('/GPU:0'):
                                                test = tf.constant([1.0], dtype=tf.float32)
                                                _ = tf.identity(test)
                                            logger.info("GPU initialized with XLA/JIT (libdevice configured)")
                                        except Exception as xla_err:
                                            logger.warning(f"GPU still failing with XLA: {xla_err}")
                                            logger.info("   Disabling XLA and retrying GPU without JIT...")
                                            try:
                                                tf.config.optimizer.set_jit(False)
                                                os.environ['TF_XLA_FLAGS'] = '--tf_xla_enable_xla_devices=false'
                                                with tf.device('/GPU:0'):
                                                    test = tf.constant([1.0], dtype=tf.float32)
                                                    _ = tf.identity(test)
                                                logger.info("GPU initialized without XLA/JIT")
                                            except Exception as no_xla_err:
                                                logger.warning(f"GPU failed even without XLA: {no_xla_err}")
                                                logger.info("   Forcing CPU mode for DeepFace")
                                                os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
                                    else:
                                        # GPU/CuDNN issue - force CPU
                                        logger.warning(f"GPU available but error detected: {gpu_err}")
                                        logger.info("   Forcing CPU mode for DeepFace (GPU will be disabled)")
                                        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'  # Disable GPU
                                        logger.info("   DeepFace will use CPU (slower but reliable)")
                        else:
                            logger.warning("No GPU devices found, using CPU")
                    except Exception as gpu_check_err:
                        logger.warning(f"GPU check failed: {gpu_check_err}")
                        logger.info("   Using CPU mode for DeepFace")
                        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'  # Disable GPU
                    
                    # Verify DeepFace is actually importable
                    from deepface import DeepFace
                    logger.info("DeepFace initialized successfully - InsightFace will NOT be used")
                    # Explicitly set app to None to prevent InsightFace usage
                    self.app = None
                except Exception as e:
                    logger.error(f"DeepFace initialization failed: {e}")
                    logger.info(f"   Error type: {type(e).__name__}")
                    import traceback
                    logger.info(f"   Traceback: {traceback.format_exc()}")
                    # Try CPU-only mode as last resort
                    try:
                        import os
                        import tensorflow as tf
                        
                        # Force CPU mode by hiding GPU devices from TensorFlow
                        # This is more reliable than CUDA_VISIBLE_DEVICES after TF is imported
                        try:
                            tf.config.set_visible_devices([], 'GPU')
                            logger.info("GPU devices hidden from TensorFlow")
                        except Exception:
                            pass
                        
                        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
                        from deepface import DeepFace
                        logger.info("DeepFace initialized in CPU-only mode")
                        self.app = None
                        self.gpu_failed = True  # Mark GPU as failed so future calls use CPU
                    except Exception as e2:
                        logger.error(f"DeepFace CPU mode also failed: {e2}")
                        logger.info("Falling back to InsightFace only because DeepFace failed")
                        self.use_deepface = False
                        self.enabled = INSIGHTFACE_AVAILABLE
            
            if not self.use_deepface and INSIGHTFACE_AVAILABLE:
                # Fallback to InsightFace only if DeepFace is not available
                logger.info("Initializing InsightFace (fallback - DeepFace not available)...")
                try:
                    import onnxruntime
                    providers = onnxruntime.get_available_providers()
                    logger.info(f"InsightFace using providers: {providers}")
                    
                    self.app = FaceAnalysis(name='buffalo_l', providers=providers)
                    self.app.prepare(ctx_id=0, det_size=(640, 640))
                    logger.info("InsightFace initialized")
                except Exception as e:
                    logger.error(f"InsightFace initialization failed: {e}")
                    self.enabled = False
            
            if self.enabled:
                self.reload_gallery()
            else:
                logger.error("Face recognition disabled - neither DeepFace nor InsightFace available")

    def reload_gallery(self):
        """Load known embeddings from DB into memory for fast lookup"""
        if not self.enabled:
            return

        with self.db._connect() as conn:
            cursor = conn.cursor()
            # Load known people
            cursor.execute("SELECT id, name FROM people WHERE is_unknown=0")
            for pid, name in cursor.fetchall():
                self.gallery_names[pid] = name
            
            # Load embeddings for known people
            cursor.execute('''
                SELECT person_id, embedding FROM person_embeddings 
                WHERE person_id IN (SELECT id FROM people WHERE is_unknown=0)
            ''')
            self.gallery = []
            for pid, blob in cursor.fetchall():
                if blob:
                    emb = pickle.loads(blob)
                    self.gallery.append((pid, emb))
            
            logger.info(f"Loaded {len(self.gallery)} embeddings for {len(self.gallery_names)} known people.")

    def recognize_face(self, crop: np.ndarray, camera_id: Optional[int] = None) -> Tuple[Optional[int], float, Optional[np.ndarray]]:
        """
        Analyze a face crop (or full image - DeepFace can detect faces automatically).
        Returns: (person_id, confidence, embedding)
        person_id is None if unknown.
        
        Args:
            crop: Image crop or full image containing a face
            camera_id: Optional camera ID for profiling/error tracking
        """
        if not self.enabled:
            return None, 0.0, None

        embedding = None
        
        if self.use_deepface:
            # Request GPU access for face recognition (high priority)
            gpu_granted = False
            resource_manager = None
            try:
                from .resource_manager import get_resource_manager, ResourcePriority
                resource_manager = get_resource_manager()
                
                # Check GPU memory BEFORE requesting access - DeepFace can allocate 3-5GB!
                gpu_stats = resource_manager.monitor.get_gpu_stats()
                if gpu_stats:
                    # Very conservative threshold - skip if GPU memory > 60% to prevent OOM
                    if gpu_stats.memory_utilization_percent > 60:
                        logger.warning(f"Skipping face recognition - GPU memory too high ({gpu_stats.memory_utilization_percent:.1f}%)")
                        return None, 0.0, None
                
                gpu_granted = resource_manager.request_gpu_access(
                    requester_id="face_recognition",
                    priority=ResourcePriority.HIGH,
                    estimated_memory_mb=3000.0,  # DeepFace/TensorFlow can use 3-5GB!
                    timeout_seconds=0.5,
                    blocking=False  # Non-blocking - skip if GPU busy
                )
            except Exception:
                # If resource manager not available, skip face recognition to be safe
                return None, 0.0, None
            
            # Skip face recognition if GPU not available (opportunistic processing)
            if not gpu_granted:
                return None, 0.0, None
            
            # Use DeepFace - it can handle full images or face crops
            try:
                # Before running TensorFlow operations, check GPU memory one more time
                if resource_manager:
                    gpu_stats = resource_manager.monitor.get_gpu_stats()
                    if gpu_stats:
                        # If GPU memory is above 60%, skip to prevent OOM (very conservative)
                        if gpu_stats.memory_utilization_percent > 60:
                            if gpu_granted:
                                try:
                                    resource_manager.release_gpu_access("face_recognition")
                                except Exception:
                                    pass
                            return None, 0.0, None
                
                # If GPU has failed before, force CPU mode
                if self.gpu_failed:
                    import os
                    import tensorflow as tf
                    try:
                        # Hide GPU devices from TensorFlow
                        tf.config.set_visible_devices([], 'GPU')
                    except Exception:
                        pass
                    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
                
                # DeepFace expects RGB, but OpenCV gives BGR
                crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                
                # Use RetinaFace detector and Facenet512 model for best accuracy
                # enforce_detection=False allows it to work with face crops
                # Check GPU memory one more time before DeepFace call (it can allocate 3-5GB!)
                if resource_manager:
                    gpu_stats = resource_manager.monitor.get_gpu_stats()
                    if gpu_stats and gpu_stats.memory_utilization_percent > 55:
                        logger.warning(f"Skipping DeepFace - GPU memory too high ({gpu_stats.memory_utilization_percent:.1f}%)")
                        if gpu_granted:
                            resource_manager.release_gpu_access("face_recognition")
                        return None, 0.0, None
                
                result = DeepFace.represent(
                    img_path=crop_rgb,
                    model_name='Facenet512',
                    detector_backend='retinaface',
                    enforce_detection=False,  # Don't fail if no face detected (for crops)
                    align=True
                )
                
                if result and len(result) > 0:
                    # Get the first (or largest) face embedding
                    embedding = np.array(result[0]['embedding'])
                else:
                    # Release GPU access before returning
                    if gpu_granted and resource_manager:
                        try:
                            resource_manager.release_gpu_access("face_recognition")
                        except Exception:
                            pass
                    return None, 0.0, None
                    
            except Exception as e:
                # Check for TensorFlow ResourceExhaustedError
                is_resource_exhausted = False
                error_str = str(e).lower()
                try:
                    import tensorflow as tf
                    if isinstance(e, tf.errors.ResourceExhaustedError):
                        is_resource_exhausted = True
                except Exception:
                    # Check error message for resource exhausted
                    if 'resource exhausted' in error_str or 'out of memory' in error_str or 'oom' in error_str:
                        is_resource_exhausted = True
                
                # Record error in profiler
                try:
                    from .profiler import get_profiler
                    cam_id = camera_id if camera_id is not None else 0
                    get_profiler().record_error(cam_id, is_resource_exhausted=is_resource_exhausted)
                except Exception:
                    pass
                
                # Log resource exhausted errors
                if is_resource_exhausted:
                    logger.warning(
                        f"Resource exhausted during face recognition "
                        f"(camera {camera_id if camera_id is not None else 'unknown'}): {e}"
                    )
                
                # Release GPU access on error
                if gpu_granted and resource_manager:
                    try:
                        resource_manager.release_gpu_access("face_recognition")
                    except Exception:
                        pass
                
                # Check for various GPU errors (CuDNN, libdevice, XLA, JIT)
                if any(keyword in error_str for keyword in ['cudnn', 'dnn', 'libdevice', 'jit compilation', 'xla', 'rsqrt', 'bottleneck_batchnorm', 'device:gpu']):
                    # GPU error - mark GPU as failed and force CPU mode
                    self.gpu_failed = True
                    logger.warning(f"GPU error detected ({type(e).__name__}), forcing CPU mode: {str(e)[:200]}")
                    try:
                        import os
                        import tensorflow as tf
                        
                        # Force TensorFlow to use CPU only by hiding GPU devices
                        # This is more reliable than CUDA_VISIBLE_DEVICES after TF is imported
                        try:
                            tf.config.set_visible_devices([], 'GPU')
                            logger.info("GPU devices hidden from TensorFlow")
                        except Exception:
                            pass
                        
                        # Also set environment variable for good measure
                        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
                        
                        # Retry with CPU - need to clear any cached models
                        # DeepFace may cache models, so we need to ensure it uses CPU
                        result = DeepFace.represent(
                            img_path=crop_rgb,
                            model_name='Facenet512',
                            detector_backend='retinaface',
                            enforce_detection=False,
                            align=True
                        )
                        if result and len(result) > 0:
                            embedding = np.array(result[0]['embedding'])
                            logger.info("DeepFace working in CPU mode (GPU disabled for this session)")
                        else:
                            return None, 0.0, None
                    except Exception as cpu_err:
                        logger.error(f"DeepFace CPU mode also failed: {cpu_err}")
                        # Fall through to InsightFace fallback
                        if INSIGHTFACE_AVAILABLE and self.app:
                            try:
                                faces = self.app.get(crop)
                                if faces:
                                    face = max(faces, key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]))
                                    embedding = face.embedding
                                else:
                                    return None, 0.0, None
                            except Exception as e2:
                                logger.info(f"Both DeepFace and InsightFace failed: {e}, {e2}")
                                return None, 0.0, None
                        else:
                            return None, 0.0, None
                else:
                    # Other error - try InsightFace fallback
                    if INSIGHTFACE_AVAILABLE and self.app:
                        try:
                            faces = self.app.get(crop)
                            if faces:
                                face = max(faces, key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]))
                                embedding = face.embedding
                            else:
                                return None, 0.0, None
                        except Exception as e2:
                            logger.info(f"Both DeepFace and InsightFace failed: {e}, {e2}")
                            return None, 0.0, None
                    else:
                        logger.info(f"DeepFace error: {e}")
                        return None, 0.0, None
        else:
            # Use InsightFace fallback
            if not self.app:
                return None, 0.0, None
                
            faces = self.app.get(crop)
            if not faces:
                return None, 0.0, None
            
            face = max(faces, key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]))
            embedding = face.embedding
        
        # Compare with gallery
        max_score = 0.0
        best_pid = None
        
        if self.gallery:
            # Vectorized cosine similarity
            # embeddings: (N, 512)
            gallery_embs = np.array([e for _, e in self.gallery])
            gallery_ids = [p for p, _ in self.gallery]
            
            # Compute similarities: dot product (vectors are normalized by ArcFace usually, but let's normalize to be safe)
            # InsightFace embeddings are usually normalized.
            sims = np.dot(gallery_embs, embedding)
            
            best_idx = np.argmax(sims)
            max_score = sims[best_idx]
            
            if max_score > self.sim_threshold:
                best_pid = gallery_ids[best_idx]
        
        # Release GPU access before returning (if we acquired it)
        if self.use_deepface and 'gpu_granted' in locals() and gpu_granted and 'resource_manager' in locals() and resource_manager:
            try:
                resource_manager.release_gpu_access("face_recognition")
            except Exception:
                pass

        return best_pid, float(max_score), embedding

    def register_unknown_face(self, embedding: np.ndarray, crop: np.ndarray, camera_id: int, timestamp: datetime, event_id: int = None) -> int:
        """Store a new unknown face embedding and image.
        
        Args:
            embedding: Face embedding vector
            crop: Face image crop
            camera_id: Camera ID
            timestamp: Detection timestamp
            event_id: Optional event ID to link this face to the original detection
        """
        if not self.enabled:
            return -1

        try:
            # 1. Save image
            ts_int = int(timestamp.timestamp())
            filename = f"face_{camera_id}_{ts_int}_{os.urandom(4).hex()}.jpg"
            rel_path = f"faces/{filename}"
            full_path = os.path.join(MEDIA_BASE_DIR, "faces", filename)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            cv2.imwrite(full_path, crop)
            
            web_path = f"{MEDIA_URL_PREFIX}/faces/{filename}"
            
            # 2. Serialize embedding
            blob = pickle.dumps(embedding)
            
            with self.db._connect() as conn:
                cursor = conn.cursor()
                # Create a placeholder person/cluster? 
                # Actually, we treat them as individual embeddings first, then cluster them later.
                # But to fit the schema, we might need a "Unclustered" person or just nullable person_id?
                # The schema has NOT NULL constraint? No, I didn't set NOT NULL.
                
                # Let's create a generic "Unknown" person entry if we want to track it immediately, 
                # OR we just insert into person_embeddings with person_id=NULL?
                # My schema: FOREIGN KEY(person_id) REFERENCES people(id). SQLite allows NULL FKs.
                
                cursor.execute('''
                    INSERT INTO person_embeddings (person_id, embedding, camera_id, timestamp, image_path, confidence, event_id)
                    VALUES (NULL, ?, ?, ?, ?, ?, ?)
                ''', (blob, camera_id, timestamp, web_path, 1.0, event_id))
                
                row_id = cursor.lastrowid
                conn.commit()
                return row_id
        except Exception as e:
            logger.info(f"Error registering face: {e}")
            return -1

    def cluster_unknowns(self):
        """
        Run DBSCAN on embeddings with person_id IS NULL.
        Create new 'people' entries (clusters) for them.
        """
        if not self.enabled:
            logger.info("Clustering skipped: Recognition disabled.")
            return

        logger.info("Starting clustering...")
        with self.db._connect() as conn:
            cursor = conn.cursor()
            
            # Fetch unassigned embeddings
            # We join with people table to ignore already known people? 
            # The logic says 'person_id IS NULL', but `register_unknown_face` sets person_id to NULL.
            # However, if we have many embeddings, we might want to filter by time?
            cursor.execute("SELECT id, embedding FROM person_embeddings WHERE person_id IS NULL")
            rows = cursor.fetchall()
            
            logger.info(f"Found {len(rows)} unassigned embeddings.")
            
            if not rows:
                return
            
            ids = []
            embeddings = []
            for r_id, blob in rows:
                try:
                    ids.append(r_id)
                    embeddings.append(pickle.loads(blob))
                except Exception as e:
                    logger.info(f"Error loading embedding {r_id}: {e}")
            
            if len(embeddings) < 2:
                logger.info("Not enough embeddings to cluster.")
                return

            X = np.array(embeddings)
            logger.info(f"Clustering {len(X)} face embeddings...")
            
            # DBSCAN
            try:
                clustering = DBSCAN(eps=0.7, min_samples=3, metric='euclidean').fit(X)
                labels = clustering.labels_
                
                unique_labels = set(labels)
                n_clusters = len(unique_labels) - (1 if -1 in unique_labels else 0)
                logger.info(f"DBSCAN found {n_clusters} clusters.")
                
                for label in unique_labels:
                    if label == -1:
                        continue
                    
                    # Create a new Person (Cluster)
                    cluster_name = f"Cluster {int(time.time())}_{label}"
                    
                    # Find representative image (first one in cluster)
                    cluster_indices = [i for i, x in enumerate(labels) if x == label]
                    if not cluster_indices:
                        continue
                        
                    first_emb_id = ids[cluster_indices[0]]
                    
                    cursor.execute("SELECT image_path FROM person_embeddings WHERE id=?", (first_emb_id,))
                    res = cursor.fetchone()
                    thumb_path = res[0] if res else None
                    
                    cursor.execute("INSERT INTO people (name, is_unknown, thumbnail_path) VALUES (?, 1, ?)", 
                                  (cluster_name, thumb_path))
                    person_id = cursor.lastrowid
                    
                    # Update embeddings
                    for idx in cluster_indices:
                        emb_id = ids[idx]
                        cursor.execute("UPDATE person_embeddings SET person_id=? WHERE id=?", (person_id, emb_id))
                
                conn.commit()
                logger.info(f"Clustering complete. Created {n_clusters} new people entries.")
                
            except Exception as e:
                logger.info(f"Clustering failed: {e}")

    def process_existing_events(self, days_lookback: int = 30, resume: bool = True):
        """
        Scan past 'person' detections in the events table and try to extract faces.
        Useful for populating the gallery from historical data.
        
        Args:
            days_lookback: How many days back to scan
            resume: If True, skip events that have already been processed
        """
        if not self.enabled:
            logger.info("Recognition disabled, cannot process existing events.")
            return {'processed': 0, 'total': 0, 'faces_found': 0, 'status': 'disabled'}

        with self._scanning_lock:
            if self._scanning_active:
                return {'processed': self._scanning_progress['processed'], 
                       'total': self._scanning_progress['total'],
                       'faces_found': self._scanning_progress['faces_found'],
                       'status': 'already_running'}
            self._scanning_active = True
            self._scanning_paused = False

        try:
            logger.info(f"Scanning past {days_lookback} days for person detections...")
            
            from .config import MEDIA_BASE_DIR, MEDIA_URL_PREFIX
            import cv2

            # Use a single connection for the entire scan to avoid locking issues
            conn = self.db._connect()
            try:
                cursor = conn.cursor()
                
                # Get person detections with full images, excluding already processed ones if resume=True
                if resume:
                    query = f"""
                        SELECT e.id, e.camera_id, e.timestamp, e.full_image_path 
                        FROM events e
                        LEFT JOIN face_scan_progress fsp ON e.id = fsp.event_id
                        WHERE e.class_name = 'person' 
                          AND e.full_image_path IS NOT NULL 
                          AND e.timestamp >= datetime('now', '-{days_lookback} days')
                          AND fsp.event_id IS NULL
                        ORDER BY e.timestamp DESC
                    """
                else:
                    query = f"""
                        SELECT id, camera_id, timestamp, full_image_path 
                        FROM events 
                        WHERE class_name = 'person' 
                          AND full_image_path IS NOT NULL 
                          AND timestamp >= datetime('now', '-{days_lookback} days')
                        ORDER BY timestamp DESC
                    """
                
                cursor.execute(query)
                rows = cursor.fetchall()
                
                total_count = len(rows)
                logger.info(f"Found {total_count} candidate detections to process.")
                
                with self._scanning_lock:
                    self._scanning_progress = {'processed': 0, 'total': total_count, 'faces_found': 0}
                
                processed_count = 0
                faces_found = 0
                
                for event_id, cam_id, ts_str, full_path_url in rows:
                    # Check for pause
                    with self._scanning_lock:
                        if self._scanning_paused:
                            logger.info(f"Scanning paused at {processed_count}/{total_count} images")
                            while self._scanning_paused and self._scanning_active:
                                time.sleep(0.5)
                            if not self._scanning_active:
                                logger.info("Scanning stopped by user")
                                break
                            logger.info(f"▶ Resuming scan from {processed_count}/{total_count} images")
                    
                    # Check if we should stop
                    with self._scanning_lock:
                        if not self._scanning_active:
                            break
                    
                    try:
                        # Convert URL path to local filesystem path
                        if not full_path_url.startswith(MEDIA_URL_PREFIX):
                            continue
                        
                        # Remove prefix and leading slash
                        rel_path = full_path_url[len(MEDIA_URL_PREFIX):].lstrip('/')
                        local_path = os.path.join(MEDIA_BASE_DIR, rel_path)
                        
                        if not os.path.exists(local_path):
                            continue
                            
                        # Parse timestamp
                        # SQLite timestamp format: "YYYY-MM-DD HH:MM:SS" or isoformat
                        try:
                            if isinstance(ts_str, str):
                                # Handle standard SQLite current_timestamp format
                                if '.' in ts_str:
                                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
                                elif '+' in ts_str:
                                    ts = datetime.fromisoformat(ts_str)
                                else:
                                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                            else:
                                ts = datetime.now() # Fallback
                        except ValueError:
                            ts = datetime.now()

                        # Load image
                        img = cv2.imread(local_path)
                        if img is None:
                            continue
                            
                        # Run face detection
                        # We pass the full image (which is already a crop of the detection usually, 
                        # but database.py says 'full_image_path' is a "full-size padded crop" or could be full frame?)
                        # _save_detection_images saves a padded crop. So it's good.
                        
                        person_id, conf, emb = self.recognize_face(img)
                        
                        # If we found a face (known or unknown), store it if it's new
                        if emb is not None:
                            # Check if we already have this image processed? 
                            # Ideally we check if there's an embedding with this source image_path?
                            # But register_unknown_face saves a NEW crop.
                            # So we might duplicate faces if we run this multiple times.
                            # Simple dedup: check if an embedding exists close to this timestamp for this camera?
                            # Or just rely on user not running it constantly.
                            
                            # Better: Check if we have an embedding record linked to this event? 
                            # We don't link events directly yet. 
                            
                            # Register face using the same connection to avoid locking
                            # Save image first
                            ts_int = int(ts.timestamp())
                            filename = f"face_{cam_id}_{ts_int}_{os.urandom(4).hex()}.jpg"
                            rel_path = f"faces/{filename}"
                            full_path = os.path.join(MEDIA_BASE_DIR, "faces", filename)
                            os.makedirs(os.path.dirname(full_path), exist_ok=True)
                            cv2.imwrite(full_path, img)
                            
                            web_path = f"{MEDIA_URL_PREFIX}/faces/{filename}"
                            blob = pickle.dumps(emb)
                            
                            # Insert embedding using same connection (with retry for transient locks)
                            max_retries = 3
                            for retry in range(max_retries):
                                try:
                                    cursor.execute('''
                                        INSERT INTO person_embeddings (person_id, embedding, camera_id, timestamp, image_path, confidence, event_id)
                                        VALUES (NULL, ?, ?, ?, ?, ?, ?)
                                    ''', (blob, cam_id, ts, web_path, 1.0, event_id))
                                    break
                                except Exception as db_err:
                                    if 'locked' in str(db_err).lower() and retry < max_retries - 1:
                                        time.sleep(0.1 * (retry + 1))  # Exponential backoff
                                        continue
                                    else:
                                        raise
                            faces_found += 1
                        
                        # Mark this event as processed (using same connection, with retry)
                        max_retries = 3
                        for retry in range(max_retries):
                            try:
                                cursor.execute("INSERT OR IGNORE INTO face_scan_progress (event_id) VALUES (?)", (event_id,))
                                break
                            except Exception as db_err:
                                if 'locked' in str(db_err).lower() and retry < max_retries - 1:
                                    time.sleep(0.1 * (retry + 1))
                                    continue
                                else:
                                    # If marking as processed fails, continue anyway
                                    pass
                        
                        processed_count += 1
                        with self._scanning_lock:
                            self._scanning_progress = {
                                'processed': processed_count,
                                'total': total_count,
                                'faces_found': faces_found
                            }
                        
                        if processed_count % 10 == 0:
                            logger.info(f"Processed {processed_count}/{total_count} images, found {faces_found} faces...")
                            # Periodic commit with retry
                            max_retries = 3
                            for retry in range(max_retries):
                                try:
                                    conn.commit()
                                    break
                                except Exception as commit_err:
                                    if 'locked' in str(commit_err).lower() and retry < max_retries - 1:
                                        time.sleep(0.2 * (retry + 1))
                                        continue
                                    else:
                                        logger.info(f"Commit error: {commit_err}")
                                        break
                            
                    except Exception as e:
                        logger.info(f"Error processing event {event_id}: {e}")
                        # Still mark as processed to avoid retrying broken images
                        try:
                            cursor.execute("INSERT OR IGNORE INTO face_scan_progress (event_id) VALUES (?)", (event_id,))
                        except Exception:
                            pass
                        continue
                
                # Final commit
                try:
                    conn.commit()
                except Exception as commit_err:
                    logger.info(f"Final commit error: {commit_err}")
                    time.sleep(0.2)
                    try:
                        conn.commit()
                    except Exception:
                        pass
                
                logger.info(f"Finished processing. Scanned {processed_count} images, registered {faces_found} faces.")
                
                result = {
                    'processed': processed_count,
                    'total': total_count,
                    'faces_found': faces_found,
                    'status': 'completed'
                }
                
                if faces_found > 0:
                    self.cluster_unknowns()
                
                return result
            finally:
                # Close the connection
                try:
                    conn.close()
                except Exception:
                    pass
                
        finally:
            with self._scanning_lock:
                self._scanning_active = False
                self._scanning_paused = False
    
    def pause_scanning(self):
        """Pause the current scanning operation"""
        with self._scanning_lock:
            if self._scanning_active:
                self._scanning_paused = True
                return True
            return False
    
    def resume_scanning(self):
        """Resume a paused scanning operation"""
        with self._scanning_lock:
            if self._scanning_paused:
                self._scanning_paused = False
                return True
            return False
    
    def stop_scanning(self):
        """Stop the current scanning operation"""
        with self._scanning_lock:
            was_active = self._scanning_active
            self._scanning_active = False
            self._scanning_paused = False
            return was_active
    
    def get_scanning_status(self):
        """Get current scanning status"""
        with self._scanning_lock:
            return {
                'active': self._scanning_active,
                'paused': self._scanning_paused,
                'progress': self._scanning_progress.copy()
            }


