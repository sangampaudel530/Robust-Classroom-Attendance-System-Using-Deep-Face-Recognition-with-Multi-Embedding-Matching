"""
services/face_detector.py
Face detection and preprocessing using InsightFace.
"""

import logging
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class FaceDetector:
    def __init__(self, app=None):
        if app is None:
            from backend.services.recognizer import get_shared_app
            app = get_shared_app()
        self.app = app

    @staticmethod
    def preprocess(image: np.ndarray) -> np.ndarray:
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    def detect(self, image: np.ndarray, is_group: bool = False, use_clahe_fallback: bool = True) -> List[Dict[str, Any]]:
        h, w = image.shape[:2]
        
        # Dynamically set detection size based on image type and size
        if is_group:
            max_dim = max(w, h)
            if max_dim > 1920:
                det_size = (1920, 1920)
            else:
                det_size = (1280, 1280)
        else:
            det_size = (640, 640)

        # Update detection size on the model dynamically
        self.app.prepare(ctx_id=-1, det_size=det_size)
        
        # Detect on the original image
        faces = self.app.get(image)
        is_clahe_used = False
        
        # If no faces are detected, fall back to CLAHE preprocessing to detect bounding boxes
        if not faces and use_clahe_fallback:
            logger.info("No faces detected on original image. Running CLAHE preprocessing fallback...")
            enhanced = self.preprocess(image)
            faces = self.app.get(enhanced)
            is_clahe_used = True

        results: List[Dict[str, Any]] = []
        for face in faces:
            x1, y1, x2, y2 = face.bbox.astype(int)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                continue

            # Always crop from the ORIGINAL image to ensure clean facial details
            face_crop = image[y1:y2, x1:x2]
            
            # If CLAHE was used, the embedding was extracted from the distorted image.
            # We set it to None here so the recognizer will extract a clean embedding from face_crop.
            embedding = getattr(face, "embedding", None) if not is_clahe_used else None
            
            results.append({
                "bbox": [x1, y1, x2, y2],
                "face_crop": face_crop,
                "embedding": embedding,
            })

        return results
