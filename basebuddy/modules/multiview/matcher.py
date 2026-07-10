"""
State-of-the-Art Feature Matching for Multiview 3D Reconstruction

Implements multiple matching strategies:
1. Classical: SIFT + FLANN with ratio test
2. Learning-based: LoFTR for dense matching (if available)
3. Hybrid: Combine multiple methods for robustness

Also includes reinforcement learning foundation for adaptive calibration.
"""

import cv2
import numpy as np
from typing import Dict, List, Tuple, Optional
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger('basebuddy.multiview.matcher')


class MatchingMethod(Enum):
    SIFT = "sift"
    ORB = "orb"
    SUPERGLUE = "superglue"  # If available
    LOFTR = "loftr"  # If available
    HYBRID = "hybrid"


@dataclass
class MatchResult:
    """Result of feature matching between two images"""
    points1: np.ndarray  # Nx2 array of points in image 1
    points2: np.ndarray  # Nx2 array of points in image 2
    confidences: np.ndarray  # N array of match confidences
    inlier_mask: np.ndarray  # N boolean array of inliers (after geometric verification)
    fundamental_matrix: Optional[np.ndarray] = None
    essential_matrix: Optional[np.ndarray] = None
    method: str = "unknown"
    processing_time_ms: float = 0.0


class FeatureMatcher:
    """
    Multi-method feature matcher with quality assessment.
    
    Supports classical (SIFT/ORB) and learning-based (LoFTR) methods.
    Automatically selects best available method.
    """
    
    def __init__(self, method: MatchingMethod = MatchingMethod.HYBRID):
        self.method = method
        self.loftr_available = self._check_loftr()
        self.superglue_available = self._check_superglue()
        
        # SIFT detector (always available)
        self.sift = cv2.SIFT_create(nfeatures=8000, contrastThreshold=0.02)
        
        # ORB detector (faster alternative)
        self.orb = cv2.ORB_create(nfeatures=5000)
        
        # FLANN matcher for SIFT
        FLANN_INDEX_KDTREE = 1
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=100)
        self.flann = cv2.FlannBasedMatcher(index_params, search_params)
        
        # BF matcher for ORB
        self.bf_matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        
        logger.info(f"FeatureMatcher initialized. LoFTR: {self.loftr_available}, SuperGlue: {self.superglue_available}")
    
    def _check_loftr(self) -> bool:
        """Check if LoFTR is available"""
        try:
            import kornia
            from kornia.feature import LoFTR
            return True
        except ImportError:
            return False
    
    def _check_superglue(self) -> bool:
        """Check if SuperGlue is available"""
        try:
            # SuperGlue typically requires specific installation
            return False  # Placeholder
        except Exception:
            return False
    
    def match(self, img1: np.ndarray, img2: np.ndarray,
              mask1: Optional[np.ndarray] = None,
              mask2: Optional[np.ndarray] = None) -> MatchResult:
        """
        Match features between two images.
        
        Args:
            img1, img2: Input images (BGR)
            mask1, mask2: Optional masks to focus on regions of interest
            
        Returns:
            MatchResult with matched points and quality metrics
        """
        import time
        start_time = time.time()
        
        # Choose method based on availability and preference
        if self.method == MatchingMethod.LOFTR and self.loftr_available:
            result = self._match_loftr(img1, img2, mask1, mask2)
        elif self.method == MatchingMethod.HYBRID:
            result = self._match_hybrid(img1, img2, mask1, mask2)
        elif self.method == MatchingMethod.ORB:
            result = self._match_orb(img1, img2, mask1, mask2)
        else:
            result = self._match_sift(img1, img2, mask1, mask2)
        
        result.processing_time_ms = (time.time() - start_time) * 1000
        
        return result
    
    def _match_sift(self, img1: np.ndarray, img2: np.ndarray,
                   mask1: Optional[np.ndarray], mask2: Optional[np.ndarray]) -> MatchResult:
        """Classical SIFT matching with ratio test"""
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
        
        # Detect and compute
        kp1, desc1 = self.sift.detectAndCompute(gray1, mask1)
        kp2, desc2 = self.sift.detectAndCompute(gray2, mask2)
        
        if desc1 is None or desc2 is None or len(desc1) < 10 or len(desc2) < 10:
            return MatchResult(
                points1=np.array([]).reshape(0, 2),
                points2=np.array([]).reshape(0, 2),
                confidences=np.array([]),
                inlier_mask=np.array([]).astype(bool),
                method="sift"
            )
        
        # Match with ratio test
        matches = self.flann.knnMatch(desc1, desc2, k=2)
        
        good_matches = []
        confidences = []
        
        for m_n in matches:
            if len(m_n) == 2:
                m, n = m_n
                ratio = m.distance / n.distance
                if ratio < 0.75:
                    good_matches.append(m)
                    confidences.append(1.0 - ratio)  # Higher is better
        
        if len(good_matches) < 8:
            return MatchResult(
                points1=np.array([]).reshape(0, 2),
                points2=np.array([]).reshape(0, 2),
                confidences=np.array([]),
                inlier_mask=np.array([]).astype(bool),
                method="sift"
            )
        
        # Extract points
        pts1 = np.float32([kp1[m.queryIdx].pt for m in good_matches])
        pts2 = np.float32([kp2[m.trainIdx].pt for m in good_matches])
        confidences = np.array(confidences)
        
        # Geometric verification with RANSAC
        F, inlier_mask = cv2.findFundamentalMat(pts1, pts2, cv2.FM_RANSAC, 1.0, 0.99)
        
        if inlier_mask is None:
            inlier_mask = np.ones(len(pts1), dtype=bool)
        else:
            inlier_mask = inlier_mask.ravel() == 1
        
        return MatchResult(
            points1=pts1,
            points2=pts2,
            confidences=confidences,
            inlier_mask=inlier_mask,
            fundamental_matrix=F,
            method="sift"
        )
    
    def _match_orb(self, img1: np.ndarray, img2: np.ndarray,
                  mask1: Optional[np.ndarray], mask2: Optional[np.ndarray]) -> MatchResult:
        """Fast ORB matching"""
        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
        
        kp1, desc1 = self.orb.detectAndCompute(gray1, mask1)
        kp2, desc2 = self.orb.detectAndCompute(gray2, mask2)
        
        if desc1 is None or desc2 is None or len(desc1) < 10 or len(desc2) < 10:
            return MatchResult(
                points1=np.array([]).reshape(0, 2),
                points2=np.array([]).reshape(0, 2),
                confidences=np.array([]),
                inlier_mask=np.array([]).astype(bool),
                method="orb"
            )
        
        matches = self.bf_matcher.knnMatch(desc1, desc2, k=2)
        
        good_matches = []
        confidences = []
        
        for m_n in matches:
            if len(m_n) == 2:
                m, n = m_n
                if m.distance < 0.75 * n.distance:
                    good_matches.append(m)
                    confidences.append(1.0 - m.distance / 256.0)
        
        if len(good_matches) < 8:
            return MatchResult(
                points1=np.array([]).reshape(0, 2),
                points2=np.array([]).reshape(0, 2),
                confidences=np.array([]),
                inlier_mask=np.array([]).astype(bool),
                method="orb"
            )
        
        pts1 = np.float32([kp1[m.queryIdx].pt for m in good_matches])
        pts2 = np.float32([kp2[m.trainIdx].pt for m in good_matches])
        confidences = np.array(confidences)
        
        F, inlier_mask = cv2.findFundamentalMat(pts1, pts2, cv2.FM_RANSAC, 1.0, 0.99)
        
        if inlier_mask is None:
            inlier_mask = np.ones(len(pts1), dtype=bool)
        else:
            inlier_mask = inlier_mask.ravel() == 1
        
        return MatchResult(
            points1=pts1,
            points2=pts2,
            confidences=confidences,
            inlier_mask=inlier_mask,
            fundamental_matrix=F,
            method="orb"
        )
    
    def _match_loftr(self, img1: np.ndarray, img2: np.ndarray,
                    mask1: Optional[np.ndarray], mask2: Optional[np.ndarray]) -> MatchResult:
        """
        LoFTR dense matching (state-of-the-art learning-based method).
        
        LoFTR (Local Feature TRansformer) provides dense correspondences
        without explicit keypoint detection, works well on textureless regions.
        """
        try:
            import torch
            import kornia
            from kornia.feature import LoFTR
            
            device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            
            # Initialize LoFTR
            matcher = LoFTR(pretrained='outdoor')
            matcher = matcher.to(device).eval()
            
            # Prepare images
            gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
            gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
            
            # Resize for LoFTR (works best at specific resolutions)
            h1, w1 = gray1.shape
            h2, w2 = gray2.shape
            
            # Scale to ~640 on longest side
            scale1 = 640 / max(h1, w1)
            scale2 = 640 / max(h2, w2)
            
            gray1_resized = cv2.resize(gray1, None, fx=scale1, fy=scale1)
            gray2_resized = cv2.resize(gray2, None, fx=scale2, fy=scale2)
            
            # Convert to tensor
            img1_tensor = torch.from_numpy(gray1_resized).float()[None, None] / 255.0
            img2_tensor = torch.from_numpy(gray2_resized).float()[None, None] / 255.0
            
            img1_tensor = img1_tensor.to(device)
            img2_tensor = img2_tensor.to(device)
            
            # Run LoFTR
            with torch.no_grad():
                batch = {'image0': img1_tensor, 'image1': img2_tensor}
                matcher(batch)
                
                mkpts0 = batch['mkpts0_f'].cpu().numpy()
                mkpts1 = batch['mkpts1_f'].cpu().numpy()
                mconf = batch['mconf'].cpu().numpy()
            
            # Scale points back to original resolution
            pts1 = mkpts0 / scale1
            pts2 = mkpts1 / scale2
            
            if len(pts1) < 8:
                return MatchResult(
                    points1=pts1,
                    points2=pts2,
                    confidences=mconf,
                    inlier_mask=np.ones(len(pts1), dtype=bool) if len(pts1) > 0 else np.array([]).astype(bool),
                    method="loftr"
                )
            
            # Geometric verification
            F, inlier_mask = cv2.findFundamentalMat(pts1, pts2, cv2.FM_RANSAC, 1.0, 0.99)
            
            if inlier_mask is None:
                inlier_mask = np.ones(len(pts1), dtype=bool)
            else:
                inlier_mask = inlier_mask.ravel() == 1
            
            return MatchResult(
                points1=pts1,
                points2=pts2,
                confidences=mconf,
                inlier_mask=inlier_mask,
                fundamental_matrix=F,
                method="loftr"
            )
            
        except Exception as e:
            logger.warning(f"LoFTR matching failed: {e}, falling back to SIFT")
            return self._match_sift(img1, img2, mask1, mask2)
    
    def _match_hybrid(self, img1: np.ndarray, img2: np.ndarray,
                     mask1: Optional[np.ndarray], mask2: Optional[np.ndarray]) -> MatchResult:
        """
        Hybrid matching: combine multiple methods for robustness.
        
        Strategy:
        1. Try LoFTR if available (best quality)
        2. Fall back to SIFT (reliable)
        3. Combine matches from multiple methods if possible
        """
        results = []
        
        # Try LoFTR first if available
        if self.loftr_available:
            try:
                loftr_result = self._match_loftr(img1, img2, mask1, mask2)
                if np.sum(loftr_result.inlier_mask) >= 20:
                    return loftr_result
                results.append(loftr_result)
            except Exception as e:
                logger.warning(f"LoFTR failed: {e}")
        
        # SIFT as primary fallback
        sift_result = self._match_sift(img1, img2, mask1, mask2)
        results.append(sift_result)
        
        # Return best result based on inlier count
        if results:
            best = max(results, key=lambda r: np.sum(r.inlier_mask))
            return best
        
        return MatchResult(
            points1=np.array([]).reshape(0, 2),
            points2=np.array([]).reshape(0, 2),
            confidences=np.array([]),
            inlier_mask=np.array([]).astype(bool),
            method="hybrid_failed"
        )
    
    def visualize_matches(self, img1: np.ndarray, img2: np.ndarray,
                         result: MatchResult, max_matches: int = 100) -> np.ndarray:
        """Create visualization of matched points"""
        h1, w1 = img1.shape[:2]
        h2, w2 = img2.shape[:2]
        
        # Create side-by-side image
        h = max(h1, h2)
        combined = np.zeros((h, w1 + w2, 3), dtype=np.uint8)
        combined[:h1, :w1] = img1
        combined[:h2, w1:] = img2
        
        # Get inlier matches
        inlier_indices = np.where(result.inlier_mask)[0]
        
        # Limit number of matches shown
        if len(inlier_indices) > max_matches:
            inlier_indices = inlier_indices[::len(inlier_indices) // max_matches]
        
        # Draw matches with color based on confidence
        for idx in inlier_indices:
            pt1 = tuple(map(int, result.points1[idx]))
            pt2 = (int(result.points2[idx][0]) + w1, int(result.points2[idx][1]))
            
            # Color based on confidence (green = high, red = low)
            conf = result.confidences[idx] if len(result.confidences) > idx else 0.5
            color = (int(255 * (1 - conf)), int(255 * conf), 0)
            
            cv2.circle(combined, pt1, 4, color, -1)
            cv2.circle(combined, pt2, 4, color, -1)
            cv2.line(combined, pt1, pt2, color, 1)
        
        # Add info text
        info_text = f"Method: {result.method} | Matches: {len(result.points1)} | Inliers: {np.sum(result.inlier_mask)}"
        cv2.putText(combined, info_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        
        return combined


class AdaptiveCalibrationRL:
    """
    Reinforcement Learning component for adaptive calibration.
    
    Learns to improve calibration by:
    - Tracking which point matches are good/bad
    - Adjusting matching thresholds
    - Guiding camera positioning suggestions
    """
    
    def __init__(self):
        self.match_history = []
        self.reward_history = []
        self.current_threshold = 0.75  # Ratio test threshold
        self.learning_rate = 0.01
    
    def record_match_quality(self, match_result: MatchResult, 
                            reconstruction_error: float = None):
        """Record a matching attempt and its quality"""
        inlier_ratio = np.sum(match_result.inlier_mask) / max(1, len(match_result.inlier_mask))
        
        self.match_history.append({
            'inlier_ratio': inlier_ratio,
            'num_matches': len(match_result.points1),
            'num_inliers': int(np.sum(match_result.inlier_mask)),
            'method': match_result.method,
            'reconstruction_error': reconstruction_error
        })
        
        # Compute reward
        if reconstruction_error is not None:
            # Reward = high inliers, low reprojection error
            reward = inlier_ratio - 0.1 * reconstruction_error
        else:
            reward = inlier_ratio
        
        self.reward_history.append(reward)
        
        # Update threshold based on reward
        self._update_threshold(reward)
    
    def _update_threshold(self, reward: float):
        """Adjust ratio test threshold based on performance"""
        if reward < 0.3:
            # Poor performance, be less strict
            self.current_threshold = min(0.85, self.current_threshold + self.learning_rate)
        elif reward > 0.7:
            # Good performance, can be stricter
            self.current_threshold = max(0.65, self.current_threshold - self.learning_rate)
    
    def get_recommended_actions(self) -> List[str]:
        """Get recommendations for improving calibration"""
        recommendations = []
        
        if len(self.match_history) < 3:
            return ["Capture more frames from different viewpoints"]
        
        recent = self.match_history[-5:]
        avg_inliers = np.mean([h['num_inliers'] for h in recent])
        avg_ratio = np.mean([h['inlier_ratio'] for h in recent])
        
        if avg_inliers < 50:
            recommendations.append("Not enough feature matches. Try better lighting or more textured scene.")
        
        if avg_ratio < 0.3:
            recommendations.append("Low inlier ratio. Cameras may be too far apart or have little overlap.")
        
        if avg_ratio > 0.8 and avg_inliers > 200:
            recommendations.append("Excellent match quality! Ready for reconstruction.")
        
        return recommendations if recommendations else ["Calibration quality is acceptable"]
    
    def get_state(self) -> Dict:
        """Get current RL state for monitoring"""
        return {
            'current_threshold': self.current_threshold,
            'total_attempts': len(self.match_history),
            'avg_reward': np.mean(self.reward_history) if self.reward_history else 0,
            'recommendations': self.get_recommended_actions()
        }


