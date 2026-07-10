"""
Person Re-ID and Attribute Classification Module.
Uses a lightweight CNN to extract embeddings for person re-identification.
"""
import torch
import torch.nn as nn
import torchvision.transforms as T
from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights
import numpy as np
from PIL import Image
from typing import List, Optional, Dict
import logging

logger = logging.getLogger(__name__)

class PersonClassifier:
    def __init__(self, device: str = None):
        self.device = device if device else ('cuda' if torch.cuda.is_available() else 'cpu')
        logger.info(f"Initializing PersonClassifier on {self.device}...")
        
        # Load a lightweight model for feature extraction
        # MobileNetV3-Small is fast and decent for appearance features
        self.weights = MobileNet_V3_Small_Weights.DEFAULT
        self.model = mobilenet_v3_small(weights=self.weights)
        
        # Remove the classifier head to get embeddings
        # The classifier in MobileNetV3 is a Sequential; the last linear layer is at index 3
        # We want the output before the final classification
        # Actually, let's just use the features before the classifier, or replace the classifier
        # MobileNetV3 structure: features -> avgpool -> classifier
        # We will use the output of avgpool (which needs a forward hook or modification)
        
        # Simpler approach: Replace the classifier with Identity (for pooling output) 
        # But MobileNetV3 classifier includes some hardswish/dropout.
        # Let's keep the earlier layers of classifier and cut the last Linear.
        # classifier[0] = Linear, [1] = Hardswish, [2] = Dropout, [3] = Linear(out)
        self.model.classifier = nn.Sequential(
            self.model.classifier[0],
            self.model.classifier[1],
            self.model.classifier[2]
            # Removed the final projection to classes
        )
        
        self.model.to(self.device)
        self.model.eval()
        
        self.transforms = T.Compose([
            T.Resize((224, 224)),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])
        
        logger.info("PersonClassifier initialized")

    def extract_features(self, frame: np.ndarray, boxes: List[np.ndarray]) -> List[Optional[np.ndarray]]:
        """
        Extract embeddings for list of bounding boxes [x1, y1, x2, y2]
        """
        if not boxes:
            return []
            
        crops = []
        valid_indices = []
        
        h, w, _ = frame.shape
        
        for i, box in enumerate(boxes):
            x1, y1, x2, y2 = map(int, box)
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)
            
            if x2 > x1 and y2 > y1:
                crop = frame[y1:y2, x1:x2]
                # Convert BGR to RGB
                crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(crop)
                crops.append(self.transforms(pil_img))
                valid_indices.append(i)
        
        if not crops:
            return [None] * len(boxes)
            
        batch = torch.stack(crops).to(self.device)
        
        with torch.no_grad():
            features = self.model(batch)
            # Normalize features
            features = torch.nn.functional.normalize(features, p=2, dim=1)
            features = features.cpu().numpy()
            
        results = [None] * len(boxes)
        for idx, feat in zip(valid_indices, features):
            results[idx] = feat
            
        return results

    @staticmethod
    def compute_similarity(feat1: np.ndarray, feat2: np.ndarray) -> float:
        """Cosine similarity between two normalized vectors"""
        return np.dot(feat1, feat2)

import cv2




