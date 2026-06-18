"""
services/recognizer.py
Face embedding extraction and matching using InsightFace + FAISS multi-embedding gallery.

Key improvements over v1:
  - Multi-embedding: stores ALL enrollment embeddings per student (.npz), not a single mean.
  - FAISS IndexFlatIP: vector search using inner-product on L2-normalised embeddings
    (equivalent to cosine similarity) for fast, accurate matching.
  - Gallery cache: in-memory FAISS index rebuilt only on invalidation.
  - Proper embedding extraction: uses InsightFace's normed_embedding from detection results
    instead of re-running detection on tiny crops.
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

EMBED_DIR = Path(os.getenv("EMBED_DIR") or os.getenv("EMBEDDINGS_DIR") or "data/embeddings")
EMBED_DIR.mkdir(parents=True, exist_ok=True)

PHOTO_DIR = Path(os.getenv("PHOTO_DIR") or os.getenv("STUDENT_PHOTOS_DIR") or "data/student_photos")

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

_shared_app = None

# ---------------------------------------------------------------------------
# FAISS gallery cache
# ---------------------------------------------------------------------------
_gallery_index = None        # faiss.IndexFlatIP
_gallery_labels: List[str] = []   # roll_no per row, parallel to FAISS rows
_gallery_roll_set: set = set()


def invalidate_gallery() -> None:
    """Clear the cached FAISS gallery so the next match rebuilds it."""
    global _gallery_index, _gallery_labels, _gallery_roll_set
    _gallery_index = None
    _gallery_labels = []
    _gallery_roll_set = set()


def load_gallery() -> Tuple:
    """Build (or return cached) FAISS inner-product index over all stored embeddings.

    Returns (faiss_index, labels_list) where labels_list[i] is the roll_no for row i.
    """
    global _gallery_index, _gallery_labels, _gallery_roll_set

    if _gallery_index is not None:
        return _gallery_index, _gallery_labels

    try:
        import faiss
    except ImportError:
        logger.error("faiss-cpu not installed — falling back to brute-force matching.")
        return None, []

    all_embeddings: List[np.ndarray] = []
    labels: List[str] = []
    roll_set: set = set()

    for path in EMBED_DIR.glob("*.npz"):
        roll_no = path.stem
        try:
            data = np.load(path)
            embs = data["embeddings"]  # shape (N, 512)
            for emb in embs:
                norm = np.linalg.norm(emb)
                if norm < 1e-6:
                    continue
                all_embeddings.append(emb / norm)  # L2 normalise for cosine sim
                labels.append(roll_no)
            roll_set.add(roll_no)
        except Exception as exc:
            logger.warning("Failed to load embedding %s: %s", path.name, exc)

    # Fallback: also load legacy single .npy files (migration support)
    for path in EMBED_DIR.glob("*.npy"):
        roll_no = path.stem
        if roll_no in roll_set:
            continue  # already loaded from .npz
        try:
            emb = np.load(path)
            norm = np.linalg.norm(emb)
            if norm < 1e-6:
                continue
            all_embeddings.append(emb / norm)
            labels.append(roll_no)
            roll_set.add(roll_no)
        except Exception as exc:
            logger.warning("Failed to load legacy embedding %s: %s", path.name, exc)

    if not all_embeddings:
        logger.info("Gallery is empty — no embeddings loaded.")
        _gallery_index = None
        _gallery_labels = []
        _gallery_roll_set = set()
        return None, []

    matrix = np.vstack(all_embeddings).astype(np.float32)
    dim = matrix.shape[1]

    # Inner product on L2-normed vectors == cosine similarity
    index = faiss.IndexFlatIP(dim)
    index.add(matrix)

    _gallery_index = index
    _gallery_labels = labels
    _gallery_roll_set = roll_set

    logger.info(
        "FAISS gallery built: %d embeddings across %d students.",
        len(labels), len(roll_set),
    )
    return _gallery_index, _gallery_labels


# ---------------------------------------------------------------------------
# Shared InsightFace model
# ---------------------------------------------------------------------------

def get_shared_app():
    """Load InsightFace once and reuse across detector/recognizer."""
    global _shared_app
    if _shared_app is None:
        from insightface.app import FaceAnalysis

        _shared_app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _shared_app.prepare(ctx_id=-1, det_size=(640, 640))
        logger.info("InsightFace model loaded.")
    return _shared_app


# ---------------------------------------------------------------------------
# FaceRecognizer
# ---------------------------------------------------------------------------

class FaceRecognizer:
    def __init__(self, embeddings_dir: Optional[str] = None):
        self.app = get_shared_app()
        self.embeddings_dir = Path(embeddings_dir) if embeddings_dir else EMBED_DIR

    # -- Embedding extraction ------------------------------------------------

    def get_embedding(self, face_crop: np.ndarray) -> Optional[np.ndarray]:
        """Extract an embedding from a face crop image.

        This re-runs detection on the crop, which is a fallback for when the
        embedding wasn't already available from the detector.
        """
        if face_crop is None or face_crop.size == 0:
            return None

        # Resize small crops up so the detector can find the face
        h, w = face_crop.shape[:2]
        if min(h, w) < 112:
            scale = 112 / min(h, w)
            face_crop = cv2.resize(face_crop, None, fx=scale, fy=scale,
                                   interpolation=cv2.INTER_CUBIC)

        faces = self.app.get(face_crop)
        if not faces:
            return None

        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        # Prefer the L2-normalised embedding
        emb = getattr(face, "normed_embedding", None)
        if emb is None:
            emb = face.embedding
        return emb

    def get_embedding_direct(self, face_obj) -> Optional[np.ndarray]:
        """Get embedding directly from an InsightFace face object (no re-detection)."""
        emb = getattr(face_obj, "normed_embedding", None)
        if emb is None:
            emb = getattr(face_obj, "embedding", None)
        return emb

    # -- Paths ---------------------------------------------------------------

    def _embed_path_npz(self, roll_no: str) -> Path:
        return self.embeddings_dir / f"{roll_no}.npz"

    def _embed_path_npy(self, roll_no: str) -> Path:
        return self.embeddings_dir / f"{roll_no}.npy"

    # -- Enrollment quality --------------------------------------------------

    def enrollment_quality(self, roll_no: str) -> dict:
        """Report how many enrollment photos a student has and a quality label."""
        student_dir = PHOTO_DIR / roll_no
        if student_dir.exists():
            photos = sum(
                1 for f in student_dir.iterdir()
                if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
            )
        else:
            photos = 0

        # Also count stored embeddings
        npz_path = self._embed_path_npz(roll_no)
        num_embeddings = 0
        if npz_path.exists():
            try:
                data = np.load(npz_path)
                num_embeddings = len(data["embeddings"])
            except Exception:
                pass

        if num_embeddings >= 10:
            quality = "excellent"
        elif num_embeddings >= 5:
            quality = "good"
        elif num_embeddings >= 3:
            quality = "fair"
        elif num_embeddings >= 1:
            quality = "poor"
        else:
            quality = "none"

        return {"photos": photos, "embeddings": num_embeddings, "quality": quality}

    # -- Multi-embedding save/load -------------------------------------------

    def save_embeddings(self, roll_no: str, embeddings: List[np.ndarray]) -> None:
        """Save multiple embeddings for a student as .npz."""
        valid = [e for e in embeddings if e is not None and np.linalg.norm(e) > 1e-6]
        if not valid:
            return
        stacked = np.vstack([e.reshape(1, -1) for e in valid]).astype(np.float32)
        np.savez(self._embed_path_npz(roll_no), embeddings=stacked)

        # Remove legacy .npy if exists
        legacy = self._embed_path_npy(roll_no)
        if legacy.exists():
            legacy.unlink()

        invalidate_gallery()

    def load_embeddings(self, roll_no: str) -> Optional[np.ndarray]:
        """Load all embeddings for a student. Returns (N, 512) array or None."""
        npz_path = self._embed_path_npz(roll_no)
        if npz_path.exists():
            try:
                return np.load(npz_path)["embeddings"]
            except Exception:
                pass

        # Fallback to legacy .npy
        npy_path = self._embed_path_npy(roll_no)
        if npy_path.exists():
            try:
                emb = np.load(npy_path)
                return emb.reshape(1, -1)
            except Exception:
                pass
        return None

    # -- Legacy compatibility wrapper ----------------------------------------

    def save_embedding(self, roll_no: str, embedding: np.ndarray) -> None:
        """Legacy: save a single embedding (wraps save_embeddings)."""
        self.save_embeddings(roll_no, [embedding])

    def load_embedding(self, roll_no: str) -> Optional[np.ndarray]:
        """Legacy: load first embedding for a student."""
        embs = self.load_embeddings(roll_no)
        if embs is not None and len(embs) > 0:
            return embs[0]
        return None

    # -- Update student embedding from photos --------------------------------

    def update_student_embedding(self, roll_no: str) -> None:
        """Re-compute and save embeddings from all photos on disk."""
        student_dir = Path("data/student_photos") / roll_no
        if not student_dir.exists():
            self.remove_embedding(roll_no)
            return

        embeddings = []
        image_files = [
            f for f in student_dir.iterdir()
            if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]

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
                emb = getattr(best, "normed_embedding", None)
                if emb is None:
                    emb = best.embedding
                if emb is not None:
                    embeddings.append(emb)

        if embeddings:
            self.save_embeddings(roll_no, embeddings)
            logger.info("Re-enrolled student %s with %d embeddings.", roll_no, len(embeddings))
        else:
            self.remove_embedding(roll_no)
            logger.warning("No faces for student %s. Embedding removed.", roll_no)

    # -- Enrollment from embedding list --------------------------------------

    def enroll_from_embeddings(self, roll_no: str, embeddings: List[np.ndarray]) -> int:
        """Store all valid embeddings for a student (multi-embedding approach)."""
        valid = [e for e in embeddings if e is not None]
        if not valid:
            return 0
        self.save_embeddings(roll_no, valid)
        return len(valid)

    def add_embeddings(self, roll_no: str, new_embeddings: List[np.ndarray]) -> int:
        """Add additional embeddings to an existing student's gallery."""
        existing = self.load_embeddings(roll_no)
        all_embs = []
        if existing is not None:
            all_embs.extend([existing[i] for i in range(len(existing))])
        valid_new = [e for e in new_embeddings if e is not None]
        all_embs.extend(valid_new)
        if all_embs:
            self.save_embeddings(roll_no, all_embs)
        return len(valid_new)

    # -- Remove --------------------------------------------------------------

    def remove_embedding(self, roll_no: str) -> None:
        for path in [self._embed_path_npz(roll_no), self._embed_path_npy(roll_no)]:
            if path.exists():
                path.unlink()
        invalidate_gallery()

    # -- Matching (FAISS-accelerated) ----------------------------------------

    def match_against_all(
        self,
        embedding: np.ndarray,
        enrolled_rolls: List[str],
        threshold: float = 0.55,
    ) -> Tuple[Optional[str], float]:
        """Match a face embedding against the enrolled gallery.

        Uses FAISS inner-product search on L2-normed embeddings (= cosine similarity).
        Returns (best_roll_no, best_score) or (None, best_score).
        """
        if embedding is None:
            return None, 0.0

        # Normalise query
        norm = np.linalg.norm(embedding)
        if norm < 1e-6:
            return None, 0.0
        query = (embedding / norm).reshape(1, -1).astype(np.float32)

        # Use FAISS gallery if available and using default dir
        if self.embeddings_dir == EMBED_DIR:
            index, labels = load_gallery()

            if index is not None and index.ntotal > 0:
                # Search top-K (capped at total vectors)
                k = min(index.ntotal, 20)
                distances, indices = index.search(query, k)

                # Find best match per enrolled student
                best_roll: Optional[str] = None
                best_score = 0.0
                enrolled_set = set(enrolled_rolls)

                for dist, idx in zip(distances[0], indices[0]):
                    if idx < 0:
                        continue
                    roll = labels[idx]
                    if roll not in enrolled_set:
                        continue
                    score = float(dist)  # inner product on normalised = cosine sim
                    if score > best_score:
                        best_score = score
                        best_roll = roll

                if best_roll and best_score >= threshold:
                    return best_roll, best_score
                return None, best_score

        # Fallback: brute-force (for custom embeddings_dir or no FAISS)
        best_roll = None
        best_score = 0.0
        for roll in enrolled_rolls:
            stored = self.load_embeddings(roll)
            if stored is None:
                continue
            for emb in stored:
                emb_norm = np.linalg.norm(emb)
                if emb_norm < 1e-6:
                    continue
                score = float(np.dot(query[0], emb / emb_norm))
                if score > best_score:
                    best_score = score
                    best_roll = roll

        if best_roll and best_score >= threshold:
            return best_roll, best_score
        return None, best_score
