"""
3D Reconstruction Module
Performs multiview 3D reconstruction from calibrated cameras
"""

import cv2
import numpy as np
from typing import List, Dict, Tuple, Optional
import logging

logger = logging.getLogger('basebuddy.multiview.reconstruction')


class MultiviewReconstructor:
    """Performs 3D reconstruction from multiple calibrated views"""
    
    def __init__(self, multiview_calibration: Dict, calibration_dir: str = None):
        """
        Args:
            multiview_calibration: Multiview calibration data from MultiviewCalibration
            calibration_dir: Path to calibration directory (for loading intrinsics)
        """
        self.calibration = multiview_calibration
        self.reference_camera = multiview_calibration['reference_camera']
        self.camera_ids = multiview_calibration['camera_ids']
        self.calibration_dir = calibration_dir
    
    def get_projection_matrices(self) -> Dict[str, np.ndarray]:
        """
        Get projection matrices for all cameras
        
        Returns:
            Dict mapping camera_id -> 3x4 projection matrix
        """
        from .calibration import MultiviewCalibration
        
        calib_manager = MultiviewCalibration(self.calibration_dir)
        projection_matrices = {}
        
        for cam_id in self.camera_ids:
            # Load intrinsic calibration
            intrinsic = calib_manager.load_intrinsic_calibration(cam_id)
            if not intrinsic:
                logger.warning(f"No intrinsic calibration for camera {cam_id}")
                continue
            
            K = np.array(intrinsic['camera_matrix'])
            
            # Get extrinsic parameters (R, T)
            if cam_id == self.reference_camera:
                # Reference camera at origin
                R = np.eye(3)
                T = np.zeros((3, 1))
            else:
                # Get from stereo calibration
                stereo_key = f"{self.reference_camera}_{cam_id}"
                if stereo_key in self.calibration['stereo_calibrations']:
                    stereo = self.calibration['stereo_calibrations'][stereo_key]
                    R = np.array(stereo['R'])
                    T = np.array(stereo['T'])
                else:
                    logger.warning(f"No stereo calibration for {stereo_key}")
                    continue
            
            # Build projection matrix: P = K[R|T]
            RT = np.hstack([R, T])
            P = K @ RT
            
            projection_matrices[cam_id] = P
        
        return projection_matrices
    
    def triangulate_points(self, 
                          points_2d: Dict[str, np.ndarray],
                          projection_matrices: Dict[str, np.ndarray]) -> np.ndarray:
        """
        Triangulate 3D points from 2D correspondences across multiple views
        
        Args:
            points_2d: Dict mapping camera_id -> Nx2 array of 2D points
            projection_matrices: Dict mapping camera_id -> 3x4 projection matrix
            
        Returns:
            Nx3 array of 3D points
        """
        # Get camera IDs that have both points and projection matrices
        valid_cameras = [cam for cam in points_2d.keys() 
                        if cam in projection_matrices]
        
        if len(valid_cameras) < 2:
            raise ValueError("Need at least 2 views with valid data for triangulation")
        
        # For N-view triangulation, we'll use DLT (Direct Linear Transform)
        # For each point correspondence across views
        num_points = len(points_2d[valid_cameras[0]])
        points_3d = []
        
        for i in range(num_points):
            # Build linear system Ax = 0 from all views
            A = []
            for cam_id in valid_cameras:
                P = projection_matrices[cam_id]
                x, y = points_2d[cam_id][i]
                
                # Add two equations per view
                A.append(x * P[2, :] - P[0, :])
                A.append(y * P[2, :] - P[1, :])
            
            A = np.array(A)
            
            # Solve using SVD
            _, _, Vt = np.linalg.svd(A)
            X = Vt[-1]
            
            # Convert from homogeneous to 3D coordinates
            X = X / X[3]
            points_3d.append(X[:3])
        
        return np.array(points_3d)
    
    def extract_plant_features(self, 
                              images: Dict[str, np.ndarray],
                              masks: Optional[Dict[str, np.ndarray]] = None,
                              method: str = 'sift') -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
        """
        Extract features from plant images (optionally masked)
        
        Args:
            images: Dict mapping camera_id -> image
            masks: Optional dict mapping camera_id -> binary mask
            method: Feature extraction method ('sift', 'orb', 'akaze')
            
        Returns:
            Dict mapping camera_id -> (keypoints, descriptors)
        """
        if method == 'sift':
            detector = cv2.SIFT_create(nfeatures=5000)
        elif method == 'orb':
            detector = cv2.ORB_create(nfeatures=5000)
        elif method == 'akaze':
            detector = cv2.AKAZE_create()
        else:
            raise ValueError(f"Unknown feature detector: {method}")
        
        features = {}
        
        for cam_id, image in images.items():
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
            
            # Apply mask if provided
            mask = masks.get(cam_id) if masks else None
            
            # Detect features
            keypoints, descriptors = detector.detectAndCompute(gray, mask)
            
            logger.info(f"Camera {cam_id}: detected {len(keypoints)} features")
            
            features[cam_id] = (keypoints, descriptors)
        
        return features
    
    def match_features_pairwise(self,
                               features: Dict[str, Tuple],
                               ratio_threshold: float = 0.75) -> Dict[Tuple[str, str], List[cv2.DMatch]]:
        """
        Match features between all camera pairs using ratio test
        
        Args:
            features: Dict mapping camera_id -> (keypoints, descriptors)
            ratio_threshold: Lowe's ratio test threshold
            
        Returns:
            Dict mapping (cam1_id, cam2_id) -> list of matches
        """
        matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
        
        matches_dict = {}
        camera_ids = list(features.keys())
        
        for i in range(len(camera_ids)):
            for j in range(i + 1, len(camera_ids)):
                cam1_id = camera_ids[i]
                cam2_id = camera_ids[j]
                
                _, desc1 = features[cam1_id]
                _, desc2 = features[cam2_id]
                
                if desc1 is None or desc2 is None or len(desc1) == 0 or len(desc2) == 0:
                    continue
                
                # Find 2 nearest neighbors
                knn_matches = matcher.knnMatch(desc1, desc2, k=2)
                
                # Apply ratio test (Lowe's ratio test)
                good_matches = []
                for match_pair in knn_matches:
                    if len(match_pair) == 2:
                        m, n = match_pair
                        if m.distance < ratio_threshold * n.distance:
                            good_matches.append(m)
                
                matches_dict[(cam1_id, cam2_id)] = good_matches
                
                logger.info(f"Matched {cam1_id} <-> {cam2_id}: {len(good_matches)} good matches")
        
        return matches_dict
    
    def reconstruct_from_features(self,
                                 images: Dict[str, np.ndarray],
                                 masks: Optional[Dict[str, np.ndarray]] = None) -> Dict:
        """
        Complete 3D reconstruction pipeline from multiview images
        
        Args:
            images: Dict mapping camera_id -> image
            masks: Optional dict mapping camera_id -> plant segmentation mask
            
        Returns:
            Dict containing 3D points, colors, and metadata
        """
        logger.info("Starting multiview 3D reconstruction...")
        
        # 1. Extract features
        features = self.extract_plant_features(images, masks)
        
        # 2. Match features pairwise
        matches_dict = self.match_features_pairwise(features)
        
        # 3. Find point correspondences across multiple views
        # This is the most complex part - we need to find the same point across 3+ views
        # For now, we'll triangulate from pairs and then merge
        
        projection_matrices = self.get_projection_matrices()
        
        all_points_3d = []
        all_colors = []
        
        # Triangulate each stereo pair
        for (cam1_id, cam2_id), matches in matches_dict.items():
            if len(matches) < 5:  # Need at least 5 points for essential matrix
                logger.warning(f"Skipping pair {cam1_id}-{cam2_id}: only {len(matches)} matches")
                continue
            
            if cam1_id not in projection_matrices or cam2_id not in projection_matrices:
                continue
            
            keypoints1, _ = features[cam1_id]
            keypoints2, _ = features[cam2_id]
            
            # Extract matched 2D points
            pts1 = np.float32([keypoints1[m.queryIdx].pt for m in matches])
            pts2 = np.float32([keypoints2[m.trainIdx].pt for m in matches])
            
            # Filter outliers using fundamental matrix
            F, mask = cv2.findFundamentalMat(pts1, pts2, cv2.FM_RANSAC, 1.0, 0.99)
            
            if F is None:
                continue
            
            # Keep only inliers
            inlier_mask = mask.ravel() == 1
            pts1 = pts1[inlier_mask]
            pts2 = pts2[inlier_mask]
            
            logger.info(f"Pair {cam1_id}-{cam2_id}: {len(pts1)} inliers after RANSAC")
            
            # Triangulate
            points_2d = {cam1_id: pts1, cam2_id: pts2}
            points_3d = self.triangulate_points(points_2d, projection_matrices)
            
            # Get colors from first camera
            img1 = images[cam1_id]
            colors = []
            for pt in pts1:
                x, y = int(pt[0]), int(pt[1])
                if 0 <= x < img1.shape[1] and 0 <= y < img1.shape[0]:
                    color = img1[y, x]
                    colors.append(color)
                else:
                    colors.append([128, 128, 128])
            
            colors = np.array(colors)
            
            all_points_3d.append(points_3d)
            all_colors.append(colors)
        
        if len(all_points_3d) == 0:
            return {
                'success': False,
                'error': 'No valid 3D points reconstructed'
            }
        
        # Merge all points
        points_3d = np.vstack(all_points_3d)
        colors = np.vstack(all_colors)
        
        # Remove outliers (points too far from median)
        center = np.median(points_3d, axis=0)
        distances = np.linalg.norm(points_3d - center, axis=1)
        threshold = np.percentile(distances, 95)
        valid_mask = distances < threshold
        
        points_3d = points_3d[valid_mask]
        colors = colors[valid_mask]
        
        logger.info(f"Reconstruction complete: {len(points_3d)} 3D points")
        
        return {
            'success': True,
            'points_3d': points_3d,
            'colors': colors,
            'num_points': len(points_3d),
            'bounds': {
                'min': points_3d.min(axis=0).tolist(),
                'max': points_3d.max(axis=0).tolist(),
                'center': center.tolist()
            }
        }
    
    def save_point_cloud_ply(self, points_3d: np.ndarray, colors: np.ndarray, 
                            output_path: str):
        """
        Save point cloud to PLY format (can be viewed in MeshLab, CloudCompare, etc.)
        
        Args:
            points_3d: Nx3 array of 3D points
            colors: Nx3 array of RGB colors (0-255)
            output_path: Output file path
        """
        header = f"""ply
format ascii 1.0
element vertex {len(points_3d)}
property float x
property float y
property float z
property uchar red
property uchar green
property uchar blue
end_header
"""
        
        with open(output_path, 'w') as f:
            f.write(header)
            for point, color in zip(points_3d, colors):
                f.write(f"{point[0]:.6f} {point[1]:.6f} {point[2]:.6f} "
                       f"{int(color[2])} {int(color[1])} {int(color[0])}\n")  # BGR to RGB
        
        logger.info(f"Saved point cloud to {output_path}")
    
    def dense_reconstruction_stereo(self,
                                   img1: np.ndarray,
                                   img2: np.ndarray,
                                   cam1_id: str,
                                   cam2_id: str,
                                   mask1: Optional[np.ndarray] = None,
                                   mask2: Optional[np.ndarray] = None) -> Dict:
        """
        Dense stereo reconstruction using stereo matching
        Generates denser point cloud than feature-based method
        
        Args:
            img1, img2: Stereo pair images
            cam1_id, cam2_id: Camera IDs
            mask1, mask2: Optional masks to focus on plant region
            
        Returns:
            Dense point cloud result
        """
        from .calibration import MultiviewCalibration
        
        logger.info(f"Dense stereo reconstruction: {cam1_id} <-> {cam2_id}")
        
        # Load calibration
        calib_manager = MultiviewCalibration()
        intrinsic1 = calib_manager.load_intrinsic_calibration(cam1_id)
        intrinsic2 = calib_manager.load_intrinsic_calibration(cam2_id)
        
        K1 = np.array(intrinsic1['camera_matrix'])
        D1 = np.array(intrinsic1['distortion_coefficients'])
        K2 = np.array(intrinsic2['camera_matrix'])
        D2 = np.array(intrinsic2['distortion_coefficients'])
        
        # Get stereo calibration
        stereo_key = f"{cam1_id}_{cam2_id}"
        if stereo_key not in self.calibration['stereo_calibrations']:
            return {'success': False, 'error': 'No stereo calibration found'}
        
        stereo = self.calibration['stereo_calibrations'][stereo_key]
        R = np.array(stereo['R'])
        T = np.array(stereo['T'])
        
        # Rectify stereo pair
        image_size = (img1.shape[1], img1.shape[0])
        R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(
            K1, D1, K2, D2, image_size, R, T
        )
        
        # Compute rectification maps
        map1x, map1y = cv2.initUndistortRectifyMap(K1, D1, R1, P1, image_size, cv2.CV_32FC1)
        map2x, map2y = cv2.initUndistortRectifyMap(K2, D2, R2, P2, image_size, cv2.CV_32FC1)
        
        # Rectify images
        img1_rect = cv2.remap(img1, map1x, map1y, cv2.INTER_LINEAR)
        img2_rect = cv2.remap(img2, map2x, map2y, cv2.INTER_LINEAR)
        
        # Convert to grayscale
        gray1 = cv2.cvtColor(img1_rect, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2_rect, cv2.COLOR_BGR2GRAY)
        
        # Compute disparity map using Semi-Global Block Matching
        stereo_matcher = cv2.StereoSGBM_create(
            minDisparity=0,
            numDisparities=128,  # Must be divisible by 16
            blockSize=5,
            P1=8 * 3 * 5**2,
            P2=32 * 3 * 5**2,
            disp12MaxDiff=1,
            uniquenessRatio=10,
            speckleWindowSize=100,
            speckleRange=32,
            mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY
        )
        
        disparity = stereo_matcher.compute(gray1, gray2).astype(np.float32) / 16.0
        
        # Filter invalid disparities
        disparity[disparity <= 0] = 0.1  # Avoid division by zero
        
        # Reproject to 3D
        points_3d = cv2.reprojectImageTo3D(disparity, Q)
        
        # Apply mask if provided
        if mask1 is not None:
            mask1_rect = cv2.remap(mask1, map1x, map1y, cv2.INTER_NEAREST)
            valid_mask = mask1_rect > 127
        else:
            valid_mask = disparity > 0
        
        # Extract valid 3D points and colors
        points_3d_valid = points_3d[valid_mask]
        colors_valid = img1_rect[valid_mask]
        
        # Remove outliers
        center = np.median(points_3d_valid, axis=0)
        distances = np.linalg.norm(points_3d_valid - center, axis=1)
        threshold = np.percentile(distances, 95)
        final_mask = distances < threshold
        
        points_3d_final = points_3d_valid[final_mask]
        colors_final = colors_valid[final_mask]
        
        logger.info(f"Dense reconstruction: {len(points_3d_final)} points")
        
        return {
            'success': True,
            'points_3d': points_3d_final,
            'colors': colors_final,
            'num_points': len(points_3d_final),
            'disparity_map': disparity
        }

