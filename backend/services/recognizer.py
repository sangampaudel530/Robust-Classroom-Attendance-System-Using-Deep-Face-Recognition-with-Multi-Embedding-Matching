"""
services/recognizer.py
Face embedding extraction and matching using InsightFace.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

EMBED_DIR = Path(os.getenv("EMBED_DIR") or os.getenv("EMBEDDINGS_DIR") or "data/embeddings")
EMBED_DIR.mkdir(parents=True, exist_ok=True)

_shared_app = None

# In-memory cache of {roll_no: embedding} loaded from EMBED_DIR.
# Avoids re-reading every .npy from disk on each recognition request.
_gallery: Optional[dict] = None


def invalidate_gallery() -> None:
    """Clear the cached embedding gallery.

    Call this after any change to enrolled embeddings (enroll, re-enroll,
    or removal) so the next match reloads fresh data from disk.
    """
    global _gallery
    _gallery = None


def load_gallery() -> dict:
    """Return {roll_no: embedding} for all embeddings in EMBED_DIR, cached."""
    global _gallery
    if _gallery is None:
        gallery: dict = {}
        for path in EMBED_DIR.glob("*.npy"):
            try:
                gallery[path.stem] = np.load(path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load embedding %s: %s", path.name, exc)
        _gallery = gallery
        logger.info("Loaded embedding gallery (%d students).", len(gallery))
    return _gallery


def get_shared_app():
    """Load InsightFace once and reuse across detector/recognizer."""
    global _shared_app
    if _shared_app is None:
        from insightface.app import FaceAnalysis

        _shared_app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _shared_app.prepare(ctx_id=-1, det_size=(640, 640))
        logger.info("InsightFace model loaded.")
    return _shared_app


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = a / (np.linalg.norm(a) + 1e-8)
    b = b / (np.linalg.norm(b) + 1e-8)
    return float(np.dot(a, b))


class FaceRecognizer:
    def __init__(self, embeddings_dir: Optional[str] = None):
        self.app = get_shared_app()
        self.embeddings_dir = Path(embeddings_dir) if embeddings_dir else EMBED_DIR

    def get_embedding(self, face_crop: np.ndarray) -> Optional[np.ndarray]:
        if face_crop is None or face_crop.size == 0:
            return None
        # Prepare app for small crop embedding extraction
        self.app.prepare(ctx_id=-1, det_size=(640, 640))
        faces = self.app.get(face_crop)
        if not faces:
            return None
        return faces[0].embedding

    def _embed_path(self, roll_no: str) -> Path:
        return self.embeddings_dir / f"{roll_no}.npy"

    def save_embedding(self, roll_no: str, embedding: np.ndarray) -> None:
        np.save(self._embed_path(roll_no), embedding.astype(np.float32))
        invalidate_gallery()

    def load_embedding(self, roll_no: str) -> Optional[np.ndarray]:
        path = self._embed_path(roll_no)
        if not path.exists():
            return None
        return np.load(path)

    def update_student_embedding(self, roll_no: str) -> None:
        """Re-compute and save the student's mean embedding from all photos on disk."""
        student_dir = Path("data/student_photos") / roll_no
        if not student_dir.exists():
            self.remove_embedding(roll_no)
            return

        embeddings = []
        image_files = [
            f for f in student_dir.iterdir()
            if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]
        
        # Prepare for close-up photo face extraction
        self.app.prepare(ctx_id=-1, det_size=(640, 640))
        
        for img_path in image_files:
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            faces = self.app.get(img)
            if faces:
                best = max(
                    faces,
                    key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
                )
                embeddings.append(best.embedding)
        
        if embeddings:
            self.enroll_from_embeddings(roll_no, embeddings)
            logger.info(f"Re-enrolled student {roll_no} with {len(embeddings)} photos.")
        else:
            self.remove_embedding(roll_no)
            logger.warning(f"No faces detected in photos for student {roll_no}. Embedding removed.")

    def enroll_from_embeddings(self, roll_no: str, embeddings: List[np.ndarray]) -> int:
        valid = [e for e in embeddings if e is not None]
        if not valid:
            return 0
        mean_emb = np.mean(valid, axis=0)
        self.save_embedding(roll_no, mean_emb)
        return len(valid)

    def remove_embedding(self, roll_no: str) -> None:
        path = self._embed_path(roll_no)
        if path.exists():
            path.unlink()
        invalidate_gallery()

    def match_against_all(
        self,
        embedding: np.ndarray,
        enrolled_rolls: List[str],
        threshold: float = 0.45,
    ) -> Tuple[Optional[str], float]:
        best_roll: Optional[str] = None
        best_score = 0.0

        # Use the cached gallery for the default store; fall back to per-file
        # reads when a custom embeddings_dir is in use (e.g. training scripts).
        gallery = load_gallery() if self.embeddings_dir == EMBED_DIR else None

        for roll in enrolled_rolls:
            stored = gallery.get(roll) if gallery is not None else self.load_embedding(roll)
            if stored is None:
                continue
            score = _cosine_similarity(embedding, stored)
            if score > best_score:
                best_score = score
                best_roll = roll

        if best_roll and best_score >= threshold:
            return best_roll, best_score
        return None, best_score
