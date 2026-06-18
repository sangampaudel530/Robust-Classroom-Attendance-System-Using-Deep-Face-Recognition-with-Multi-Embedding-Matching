"""
services/anti_spoof.py
Basic anti-spoofing using Laplacian variance heuristics.
"""

import logging
import os
from typing import Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class AntiSpoofing:
    def __init__(self, spoof_threshold: float = None):
        if spoof_threshold is None:
            spoof_threshold = float(os.getenv("SPOOF_THRESHOLD") or os.getenv("SPOOF_THRESHOLD", 0.55))
        self.spoof_threshold = spoof_threshold

    def is_real(self, image: np.ndarray, bbox: list, is_group: bool = False) -> Tuple[bool, float]:
        if is_group:
            # Bypass spoofing checks for classroom group photos as distance/softness makes it inaccurate
            return True, 1.0

        x1, y1, x2, y2 = [int(v) for v in bbox]
        
        # For small face crops, Laplacian variance is not a reliable spoof check
        face_width = x2 - x1
        face_height = y2 - y1
        if face_width < 100 or face_height < 100:
            return True, 1.0

        face = image[y1:y2, x1:x2]
        if face.size == 0:
            return False, 0.0

        gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        # Normalize rough sharpness score into 0–1 range
        score = min(1.0, lap_var / 500.0)
        is_real = score >= self.spoof_threshold
        
        logger.info(
            f"Spoof check | BBox: [{x1},{y1},{x2},{y2}] ({face_width}x{face_height}) | "
            f"Laplacian Var: {lap_var:.1f} | Score: {score:.3f} | Threshold: {self.spoof_threshold} | "
            f"Result: {'REAL' if is_real else 'SPOOF'}"
        )
        return is_real, float(score)
