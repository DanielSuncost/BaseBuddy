"""
Camera Calibration Module
Handles intrinsic and extrinsic calibration for multiview 3D reconstruction

Supports two calibration approaches:
1. Checkerboard-based: Traditional, high accuracy, requires calibration pattern
2. Auto-calibration (SfM): Uses feature matching on scene, no pattern needed
"""

import os
import json
import cv2
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import logging

logger = logging.getLogger('basebuddy.multiview.calibration')


class CameraCalibrator:
    """Handles single camera intrinsic calibration using checkerboard pattern"""
    
    def __init__(self, 
                 checkerboard_size: Tuple[int, int] = (9, 6),
                 square_size_mm: float = 25.0):
        """
        Args:
            checkerboard_size: Number of INNER corners (not squares) - (width, height)
            square_size_mm: Size of each square in millimeters
        """
        self.checkerboard_size = checkerboard_size
        self.square_size_mm = square_size_mm
        
        # Prepare object points (0,0,0), (1,0,0), (2,0,0) ... scaled by square size
        self.objp = np.zeros((checkerboard_size[0] * checkerboard_size[1], 3), np.float32)
        self.objp[:, :2] = np.mgrid[0:checkerboard_size[0], 
                                     0:checkerboard_size[1]].T.reshape(-1, 2)
        self.objp *= square_size_mm
        
        # Termination criteria for corner refinement
        self.criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    
    def find_checkerboard_corners(self, image: np.ndarray) -> Optional[np.ndarray]:
        """
        Find checkerboard corners in an image
        
        Args:
            image: Input image (BGR or grayscale)
            
        Returns:
            Refined corner positions or None if not found
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        
        # Find checkerboard corners
        ret, corners = cv2.findChessboardCorners(
            gray, 
            self.checkerboard_size,
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_FAST_CHECK + cv2.CALIB_CB_NORMALIZE_IMAGE
        )
        
        if ret:
            # Refine corner positions to subpixel accuracy
            corners_refined = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), self.criteria)
            return corners_refined
        
        return None
    
    def calibrate_camera(self, images: List[np.ndarray]) -> Dict:
        """
        Calibrate camera from multiple checkerboard images
        
        Args:
            images: List of images showing checkerboard from different angles
            
        Returns:
            Calibration result dictionary with camera matrix, distortion coefficients, etc.
        """
        obj_points = []  # 3D points in real world space
        img_points = []  # 2D points in image plane
        
        successful_images = 0
        image_size = None
        
        for idx, image in enumerate(images):
            if image_size is None:
                image_size = (image.shape[1], image.shape[0])
            
            corners = self.find_checkerboard_corners(image)
            
            if corners is not None:
                obj_points.append(self.objp)
                img_points.append(corners)
                successful_images += 1
                logger.info(f"Found checkerboard in image {idx + 1}/{len(images)}")
            else:
                logger.warning(f"Could not find checkerboard in image {idx + 1}/{len(images)}")
        
        if successful_images < 10:
            raise ValueError(
                f"Only {successful_images} valid images found. "
                f"Need at least 10 for reliable calibration."
            )
        
        logger.info(f"Calibrating with {successful_images} images...")
        
        # Calibrate camera
        ret, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
            obj_points, img_points, image_size, None, None
        )
        
        # Calculate reprojection error
        total_error = 0
        for i in range(len(obj_points)):
            img_points_reprojected, _ = cv2.projectPoints(
                obj_points[i], rvecs[i], tvecs[i], camera_matrix, dist_coeffs
            )
            error = cv2.norm(img_points[i], img_points_reprojected, cv2.NORM_L2) / len(img_points_reprojected)
            total_error += error
        
        mean_error = total_error / len(obj_points)
        
        logger.info(f"Calibration complete. Mean reprojection error: {mean_error:.3f} pixels")
        
        return {
            'success': True,
            'camera_matrix': camera_matrix.tolist(),
            'distortion_coefficients': dist_coeffs.tolist(),
            'image_size': image_size,
            'num_images_used': successful_images,
            'mean_reprojection_error': float(mean_error),
            'calibration_date': datetime.now().isoformat()
        }
    
    def draw_corners(self, image: np.ndarray, corners: np.ndarray) -> np.ndarray:
        """Draw detected corners on image for visualization"""
        output = image.copy()
        cv2.drawChessboardCorners(output, self.checkerboard_size, corners, True)
        return output


class MultiviewCalibration:
    """Handles extrinsic calibration between multiple cameras for 3D reconstruction"""
    
    def __init__(self, calibration_dir: str = "multiview_calibration"):
        """
        Args:
            calibration_dir: Directory to store calibration data
        """
        self.calibration_dir = calibration_dir
        Path(calibration_dir).mkdir(parents=True, exist_ok=True)
    
    def save_intrinsic_calibration(self, camera_id: str, calibration: Dict):
        """Save intrinsic calibration for a camera"""
        filepath = os.path.join(self.calibration_dir, f"camera_{camera_id}_intrinsic.json")
        with open(filepath, 'w') as f:
            json.dump(calibration, f, indent=2)
        logger.info(f"Saved intrinsic calibration for camera {camera_id}")
    
    def load_intrinsic_calibration(self, camera_id: str) -> Optional[Dict]:
        """Load intrinsic calibration for a camera"""
        filepath = os.path.join(self.calibration_dir, f"camera_{camera_id}_intrinsic.json")
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return json.load(f)
        return None
    
    def calibrate_stereo_pair(self, 
                              camera1_id: str,
                              camera2_id: str,
                              images1: List[np.ndarray],
                              images2: List[np.ndarray],
                              checkerboard_size: Tuple[int, int] = (9, 6),
                              square_size_mm: float = 25.0) -> Dict:
        """
        Calibrate stereo pair to find relative position/rotation
        
        Args:
            camera1_id: ID of first camera
            camera2_id: ID of second camera
            images1: Synchronized checkerboard images from camera 1
            images2: Synchronized checkerboard images from camera 2
            checkerboard_size: Checkerboard inner corners
            square_size_mm: Square size in mm
            
        Returns:
            Stereo calibration result with R, T, E, F matrices
        """
        if len(images1) != len(images2):
            raise ValueError("Must have same number of images from both cameras")
        
        # Load or compute intrinsic calibrations
        calib1 = self.load_intrinsic_calibration(camera1_id)
        calib2 = self.load_intrinsic_calibration(camera2_id)
        
        if not calib1 or not calib2:
            raise ValueError("Both cameras must be intrinsically calibrated first")
        
        camera_matrix1 = np.array(calib1['camera_matrix'])
        dist_coeffs1 = np.array(calib1['distortion_coefficients'])
        camera_matrix2 = np.array(calib2['camera_matrix'])
        dist_coeffs2 = np.array(calib2['distortion_coefficients'])
        
        # Find checkerboard corners in synchronized image pairs
        calibrator = CameraCalibrator(checkerboard_size, square_size_mm)
        
        obj_points = []
        img_points1 = []
        img_points2 = []
        
        for idx, (img1, img2) in enumerate(zip(images1, images2)):
            corners1 = calibrator.find_checkerboard_corners(img1)
            corners2 = calibrator.find_checkerboard_corners(img2)
            
            if corners1 is not None and corners2 is not None:
                obj_points.append(calibrator.objp)
                img_points1.append(corners1)
                img_points2.append(corners2)
                logger.info(f"Found checkerboard in both cameras, pair {idx + 1}/{len(images1)}")
            else:
                logger.warning(f"Checkerboard not found in both views for pair {idx + 1}")
        
        if len(obj_points) < 10:
            raise ValueError(f"Only {len(obj_points)} valid pairs. Need at least 10.")
        
        logger.info(f"Performing stereo calibration with {len(obj_points)} image pairs...")
        
        # Stereo calibration
        image_size = (images1[0].shape[1], images1[0].shape[0])
        
        ret, _, _, _, _, R, T, E, F = cv2.stereoCalibrate(
            obj_points,
            img_points1,
            img_points2,
            camera_matrix1,
            dist_coeffs1,
            camera_matrix2,
            dist_coeffs2,
            image_size,
            criteria=(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-5),
            flags=cv2.CALIB_FIX_INTRINSIC  # Use pre-calibrated intrinsics
        )
        
        logger.info(f"Stereo calibration complete. RMS error: {ret:.3f}")
        
        result = {
            'success': True,
            'camera1_id': camera1_id,
            'camera2_id': camera2_id,
            'R': R.tolist(),  # Rotation matrix
            'T': T.tolist(),  # Translation vector
            'E': E.tolist(),  # Essential matrix
            'F': F.tolist(),  # Fundamental matrix
            'rms_error': float(ret),
            'num_pairs_used': len(obj_points),
            'calibration_date': datetime.now().isoformat()
        }
        
        # Save stereo calibration
        filepath = os.path.join(
            self.calibration_dir, 
            f"stereo_{camera1_id}_{camera2_id}.json"
        )
        with open(filepath, 'w') as f:
            json.dump(result, f, indent=2)
        
        logger.info(f"Saved stereo calibration: {camera1_id} <-> {camera2_id}")
        
        return result
    
    def calibrate_multiview(self, 
                           camera_ids: List[str],
                           synchronized_images: Dict[str, List[np.ndarray]],
                           checkerboard_size: Tuple[int, int] = (9, 6),
                           square_size_mm: float = 25.0) -> Dict:
        """
        Calibrate multiple cameras for 3D reconstruction
        Uses first camera as world coordinate system reference
        
        Args:
            camera_ids: List of camera IDs (first one is reference)
            synchronized_images: Dict mapping camera_id -> list of synchronized images
            checkerboard_size: Checkerboard dimensions
            square_size_mm: Square size in mm
            
        Returns:
            Complete multiview calibration
        """
        if len(camera_ids) < 2:
            raise ValueError("Need at least 2 cameras for multiview")
        
        reference_cam = camera_ids[0]
        logger.info(f"Using camera {reference_cam} as world coordinate reference")
        
        # Build calibration graph - calibrate each camera pair with reference
        stereo_calibrations = {}
        
        for cam_id in camera_ids[1:]:
            logger.info(f"Calibrating stereo pair: {reference_cam} <-> {cam_id}")
            
            stereo_calib = self.calibrate_stereo_pair(
                reference_cam,
                cam_id,
                synchronized_images[reference_cam],
                synchronized_images[cam_id],
                checkerboard_size,
                square_size_mm
            )
            
            stereo_calibrations[f"{reference_cam}_{cam_id}"] = stereo_calib
        
        # Build complete calibration structure
        multiview_calib = {
            'success': True,
            'reference_camera': reference_cam,
            'camera_ids': camera_ids,
            'stereo_calibrations': stereo_calibrations,
            'calibration_date': datetime.now().isoformat()
        }
        
        # Save complete multiview calibration
        filepath = os.path.join(self.calibration_dir, "multiview_calibration.json")
        with open(filepath, 'w') as f:
            json.dump(multiview_calib, f, indent=2)
        
        logger.info(f"Multiview calibration complete for {len(camera_ids)} cameras")
        
        return multiview_calib
    
    def load_multiview_calibration(self) -> Optional[Dict]:
        """Load complete multiview calibration"""
        filepath = os.path.join(self.calibration_dir, "multiview_calibration.json")
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return json.load(f)
        return None
    
    def get_camera_pose(self, camera_id: str) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """
        Get camera pose (R, T) relative to reference camera
        
        Returns:
            (R, T) tuple: rotation matrix and translation vector, or None if not calibrated
        """
        multiview_calib = self.load_multiview_calibration()
        if not multiview_calib:
            return None
        
        reference_cam = multiview_calib['reference_camera']
        
        if camera_id == reference_cam:
            # Reference camera is at origin
            return np.eye(3), np.zeros((3, 1))
        
        # Look up stereo calibration
        stereo_key = f"{reference_cam}_{camera_id}"
        if stereo_key in multiview_calib['stereo_calibrations']:
            stereo = multiview_calib['stereo_calibrations'][stereo_key]
            R = np.array(stereo['R'])
            T = np.array(stereo['T'])
            return R, T
        
        return None


class AutoCalibrator:
    """
    Automatic camera calibration using feature matching (Structure from Motion approach)
    
    This allows calibrating cameras WITHOUT a checkerboard pattern by using
    feature correspondences on the scene itself (e.g., the plant).
    
    Best for: Inward-facing cameras where showing checkerboard to all is impractical
    
    Limitations:
    - Requires textured scene (plants with leaves work well)
    - Scale is unknown (relative positions only, unless reference object size is known)
    - Slightly less accurate than checkerboard for intrinsics
    """
    
    def __init__(self, calibration_dir: str = "multiview_calibration"):
        self.calibration_dir = calibration_dir
        Path(calibration_dir).mkdir(parents=True, exist_ok=True)
        
        # SIFT feature detector (best for this purpose)
        self.detector = cv2.SIFT_create(nfeatures=8000)
        
        # FLANN-based matcher
        FLANN_INDEX_KDTREE = 1
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=50)
        self.matcher = cv2.FlannBasedMatcher(index_params, search_params)
    
    def estimate_intrinsics_from_image(self, image: np.ndarray) -> np.ndarray:
        """
        Estimate reasonable camera intrinsics from image dimensions
        
        This is an approximation - for best results, use checkerboard for intrinsics
        """
        h, w = image.shape[:2]
        
        # Assume FOV around 60-70 degrees (typical for webcams)
        # f = w / (2 * tan(fov/2))
        fov_rad = np.radians(65)  # Assume 65 degree FOV
        focal_length = w / (2 * np.tan(fov_rad / 2))
        
        camera_matrix = np.array([
            [focal_length, 0, w / 2],
            [0, focal_length, h / 2],
            [0, 0, 1]
        ], dtype=np.float32)
        
        return camera_matrix
    
    def find_matches(self, 
                    img1: np.ndarray, 
                    img2: np.ndarray,
                    mask1: Optional[np.ndarray] = None,
                    mask2: Optional[np.ndarray] = None,
                    ratio_threshold: float = 0.7) -> Tuple[np.ndarray, np.ndarray]:
        """
        Find feature matches between two images
        
        Args:
            img1, img2: Input images (BGR)
            mask1, mask2: Optional masks to focus on plant region
            ratio_threshold: Lowe's ratio test threshold
            
        Returns:
            (pts1, pts2): Matching point coordinates
        """
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
        
        # Detect features
        kp1, desc1 = self.detector.detectAndCompute(gray1, mask1)
        kp2, desc2 = self.detector.detectAndCompute(gray2, mask2)
        
        logger.info(f"Detected {len(kp1)} and {len(kp2)} features")
        
        if desc1 is None or desc2 is None or len(desc1) < 10 or len(desc2) < 10:
            return np.array([]), np.array([])
        
        # Match features
        matches = self.matcher.knnMatch(desc1, desc2, k=2)
        
        # Ratio test
        good_matches = []
        for m_n in matches:
            if len(m_n) == 2:
                m, n = m_n
                if m.distance < ratio_threshold * n.distance:
                    good_matches.append(m)
        
        logger.info(f"Found {len(good_matches)} good matches after ratio test")
        
        if len(good_matches) < 8:
            return np.array([]), np.array([])
        
        # Extract point coordinates
        pts1 = np.float32([kp1[m.queryIdx].pt for m in good_matches])
        pts2 = np.float32([kp2[m.trainIdx].pt for m in good_matches])
        
        return pts1, pts2
    
    def estimate_relative_pose(self,
                              img1: np.ndarray,
                              img2: np.ndarray,
                              K1: Optional[np.ndarray] = None,
                              K2: Optional[np.ndarray] = None,
                              mask1: Optional[np.ndarray] = None,
                              mask2: Optional[np.ndarray] = None) -> Dict:
        """
        Estimate relative camera pose between two views using feature matching
        
        This is the core of auto-calibration - no checkerboard needed!
        
        Args:
            img1, img2: Input images
            K1, K2: Camera intrinsic matrices (if None, will estimate)
            mask1, mask2: Optional masks for plant region
            
        Returns:
            Dict with R, T, inlier count, and quality metrics
        """
        # Estimate intrinsics if not provided
        if K1 is None:
            K1 = self.estimate_intrinsics_from_image(img1)
        if K2 is None:
            K2 = self.estimate_intrinsics_from_image(img2)
        
        # Find feature matches
        pts1, pts2 = self.find_matches(img1, img2, mask1, mask2)
        
        if len(pts1) < 6:
            return {
                'success': False,
                'error': f'Not enough matches: {len(pts1)}. Need at least 6.'
            }
        
        # Compute Essential matrix using RANSAC
        E, mask = cv2.findEssentialMat(
            pts1, pts2, K1,
            method=cv2.RANSAC,
            prob=0.999,
            threshold=1.0
        )
        
        if E is None:
            return {
                'success': False,
                'error': 'Could not estimate Essential matrix'
            }
        
        # Count inliers
        inlier_mask = mask.ravel() == 1
        num_inliers = np.sum(inlier_mask)
        inlier_ratio = num_inliers / len(pts1)
        
        logger.info(f"Essential matrix: {num_inliers}/{len(pts1)} inliers ({inlier_ratio*100:.1f}%)")
        
        # Minimum 5 inliers for essential matrix decomposition (5-point algorithm)
        if num_inliers < 5:
            return {
                'success': False,
                'error': f'Too few inliers: {num_inliers}. Need at least 5.'
            }
        
        if num_inliers < 10:
            logger.warning(f"Low inlier count ({num_inliers}) - reconstruction quality may be limited")
        
        # Recover pose (R, T) from Essential matrix
        _, R, T, pose_mask = cv2.recoverPose(E, pts1, pts2, K1, mask=mask)
        
        # Calculate quality score based on inlier ratio and count
        quality_score = min(1.0, (num_inliers / 100) * inlier_ratio)
        
        return {
            'success': True,
            'R': R,
            'T': T,
            'E': E,
            'num_matches': len(pts1),
            'num_inliers': int(num_inliers),
            'inlier_ratio': float(inlier_ratio),
            'quality_score': float(quality_score),
            'K1': K1,
            'K2': K2,
            'pts1_inliers': pts1[inlier_mask],
            'pts2_inliers': pts2[inlier_mask]
        }
    
    def auto_calibrate_stereo(self,
                             camera1_id: str,
                             camera2_id: str,
                             images1: List[np.ndarray],
                             images2: List[np.ndarray],
                             masks1: Optional[List[np.ndarray]] = None,
                             masks2: Optional[List[np.ndarray]] = None,
                             use_intrinsic_calibration: bool = True) -> Dict:
        """
        Auto-calibrate stereo pair from synchronized scene images (no checkerboard)
        
        Uses multiple image pairs for robustness
        
        Args:
            camera1_id, camera2_id: Camera IDs
            images1, images2: Lists of synchronized images
            masks1, masks2: Optional masks for each image
            use_intrinsic_calibration: If True, use saved intrinsic calibration if available
            
        Returns:
            Stereo calibration result
        """
        logger.info(f"Auto-calibrating stereo pair: {camera1_id} <-> {camera2_id}")
        logger.info(f"Using {len(images1)} synchronized image pairs")
        
        # Load intrinsic calibrations if available and requested
        K1, K2 = None, None
        if use_intrinsic_calibration:
            mc = MultiviewCalibration(self.calibration_dir)
            intr1 = mc.load_intrinsic_calibration(camera1_id)
            intr2 = mc.load_intrinsic_calibration(camera2_id)
            if intr1:
                K1 = np.array(intr1['camera_matrix'])
                logger.info(f"Using saved intrinsics for {camera1_id}")
            if intr2:
                K2 = np.array(intr2['camera_matrix'])
                logger.info(f"Using saved intrinsics for {camera2_id}")
        
        # Estimate poses from multiple image pairs
        all_results = []
        
        for i, (img1, img2) in enumerate(zip(images1, images2)):
            mask1 = masks1[i] if masks1 else None
            mask2 = masks2[i] if masks2 else None
            
            result = self.estimate_relative_pose(img1, img2, K1, K2, mask1, mask2)
            
            if result['success']:
                result['image_idx'] = i
                all_results.append(result)
                logger.info(f"Pair {i}: {result['num_inliers']} inliers, "
                           f"quality {result['quality_score']:.3f}")
        
        if len(all_results) == 0:
            return {
                'success': False,
                'error': 'Could not estimate pose from any image pair'
            }
        
        # Use result with highest quality score
        best_result = max(all_results, key=lambda x: x['quality_score'])
        
        logger.info(f"Best result from pair {best_result['image_idx']}: "
                   f"quality {best_result['quality_score']:.3f}")
        
        # Build final calibration result
        calibration = {
            'success': True,
            'method': 'auto_sfm',
            'camera1_id': camera1_id,
            'camera2_id': camera2_id,
            'R': best_result['R'].tolist(),
            'T': best_result['T'].tolist(),
            'E': best_result['E'].tolist(),
            'num_image_pairs': len(images1),
            'num_successful_pairs': len(all_results),
            'best_pair_index': best_result['image_idx'],
            'quality_score': best_result['quality_score'],
            'num_inliers': best_result['num_inliers'],
            'inlier_ratio': best_result['inlier_ratio'],
            'calibration_date': datetime.now().isoformat(),
            'note': 'Scale is relative (not absolute mm). Use known object for scale.'
        }
        
        # Save calibration
        filepath = os.path.join(
            self.calibration_dir,
            f"auto_stereo_{camera1_id}_{camera2_id}.json"
        )
        with open(filepath, 'w') as f:
            json.dump(calibration, f, indent=2)
        
        logger.info(f"Saved auto-calibration: {camera1_id} <-> {camera2_id}")
        
        return calibration
    
    def auto_calibrate_multiview(self,
                                camera_ids: List[str],
                                synchronized_images: Dict[str, List[np.ndarray]],
                                masks: Optional[Dict[str, List[np.ndarray]]] = None) -> Dict:
        """
        Auto-calibrate multiple cameras using feature matching
        
        Calibrates all camera pairs that have sufficient feature overlap
        Uses first camera as reference
        
        Args:
            camera_ids: List of camera IDs (first is reference)
            synchronized_images: Dict mapping camera_id -> list of synchronized images
            masks: Optional dict mapping camera_id -> list of masks
            
        Returns:
            Complete multiview auto-calibration
        """
        if len(camera_ids) < 2:
            return {'success': False, 'error': 'Need at least 2 cameras'}
        
        reference_cam = camera_ids[0]
        logger.info(f"Auto-calibrating multiview with reference: {reference_cam}")
        
        stereo_calibrations = {}
        failed_pairs = []
        
        # Try to calibrate each camera pair with reference
        for cam_id in camera_ids[1:]:
            logger.info(f"Auto-calibrating: {reference_cam} <-> {cam_id}")
            
            masks1 = masks.get(reference_cam) if masks else None
            masks2 = masks.get(cam_id) if masks else None
            
            stereo_result = self.auto_calibrate_stereo(
                reference_cam,
                cam_id,
                synchronized_images[reference_cam],
                synchronized_images[cam_id],
                masks1,
                masks2
            )
            
            if stereo_result['success']:
                stereo_calibrations[f"{reference_cam}_{cam_id}"] = stereo_result
            else:
                failed_pairs.append((reference_cam, cam_id, stereo_result.get('error', 'Unknown')))
        
        # Also try calibrating adjacent camera pairs (for better graph connectivity)
        for i in range(len(camera_ids)):
            for j in range(i + 1, len(camera_ids)):
                if i == 0:  # Already done with reference
                    continue
                
                cam1 = camera_ids[i]
                cam2 = camera_ids[j]
                key = f"{cam1}_{cam2}"
                
                # Skip if already have this pair
                if key in stereo_calibrations or f"{cam2}_{cam1}" in stereo_calibrations:
                    continue
                
                masks1 = masks.get(cam1) if masks else None
                masks2 = masks.get(cam2) if masks else None
                
                stereo_result = self.auto_calibrate_stereo(
                    cam1,
                    cam2,
                    synchronized_images[cam1],
                    synchronized_images[cam2],
                    masks1,
                    masks2
                )
                
                if stereo_result['success']:
                    stereo_calibrations[key] = stereo_result
        
        if len(stereo_calibrations) == 0:
            return {
                'success': False,
                'error': 'Could not calibrate any camera pairs',
                'failed_pairs': failed_pairs
            }
        
        # Build multiview calibration
        multiview_calib = {
            'success': True,
            'method': 'auto_sfm',
            'reference_camera': reference_cam,
            'camera_ids': camera_ids,
            'stereo_calibrations': stereo_calibrations,
            'num_calibrated_pairs': len(stereo_calibrations),
            'failed_pairs': failed_pairs,
            'calibration_date': datetime.now().isoformat(),
            'note': 'Scale is relative. Place known-size object in scene for absolute scale.'
        }
        
        # Save
        filepath = os.path.join(self.calibration_dir, "multiview_calibration.json")
        with open(filepath, 'w') as f:
            json.dump(multiview_calib, f, indent=2)
        
        logger.info(f"Auto-calibration complete: {len(stereo_calibrations)} pairs calibrated")
        
        return multiview_calib
    
    def visualize_matches(self,
                         img1: np.ndarray,
                         img2: np.ndarray,
                         pts1: np.ndarray,
                         pts2: np.ndarray,
                         max_matches: int = 50) -> np.ndarray:
        """Create visualization of feature matches between images"""
        h1, w1 = img1.shape[:2]
        h2, w2 = img2.shape[:2]
        
        # Create side-by-side image
        h = max(h1, h2)
        combined = np.zeros((h, w1 + w2, 3), dtype=np.uint8)
        combined[:h1, :w1] = img1
        combined[:h2, w1:] = img2
        
        # Draw matches
        colors = np.random.randint(0, 255, (len(pts1), 3)).tolist()
        
        for i, (pt1, pt2) in enumerate(zip(pts1[:max_matches], pts2[:max_matches])):
            p1 = (int(pt1[0]), int(pt1[1]))
            p2 = (int(pt2[0]) + w1, int(pt2[1]))
            color = tuple(colors[i])
            
            cv2.circle(combined, p1, 5, color, -1)
            cv2.circle(combined, p2, 5, color, -1)
            cv2.line(combined, p1, p2, color, 1)
        
        # Add text
        cv2.putText(combined, f"Matches: {len(pts1)}", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        
        return combined

