"""
services/anti_spoof.py
Liveness / anti-spoofing using the Silent-Face MiniFASNet ensemble (ONNX).

Key improvements:
  - Configurable mode: disabled / advisory / enforce (via ANTI_SPOOF_MODE env var)
  - Disabled by default for teacher-uploaded photos (teacher = trusted party)
  - Raised minimum face size to 100px (below this, MiniFASNet can't judge reliably)
  - Lower default threshold (0.35) to reduce false rejections
  - Better logging for debugging
"""

import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)

MODEL_DIR = Path(os.getenv("ANTI_SPOOF_MODEL_DIR", "models/anti_spoof"))

# Minimum real-face probability (0-1) required to accept a face as live.
DEFAULT_SPOOF_THRESHOLD = float(os.getenv("SPOOF_THRESHOLD", "0.35"))

# Mode: "disabled" | "advisory" | "enforce"
#   disabled  — always returns (True, 1.0); anti-spoofing is off
#   advisory  — runs the check, logs warnings, but never blocks attendance
#   enforce   — blocks faces classified as spoof from attendance
ANTI_SPOOF_MODE = os.getenv("ANTI_SPOOF_MODE", "disabled").lower()

# Faces smaller than this (pixels, min side) are too low-resolution for MiniFASNet.
# Accept them as real to avoid penalising distant students in classroom photos.
MIN_FACE_SIZE = int(os.getenv("SPOOF_MIN_FACE", "100"))


def _parse_scale(model_name: str) -> Optional[float]:
    """Extract the crop scale encoded in a Silent-Face model filename.

    e.g. '2.7_80x80_MiniFASNetV2.onnx' -> 2.7, 'org_..' -> None.
    """
    head = model_name.split("_")[0]
    if head == "org":
        return None
    try:
        return float(head)
    except ValueError:
        return None


def _crop(image: np.ndarray, bbox_xywh: List[int], scale: Optional[float],
          out_w: int, out_h: int) -> np.ndarray:
    """Crop a context box around the face and resize to (out_w, out_h).

    Port of minivision-ai CropImage: expands the face box by `scale`, clamps to
    the image, and resizes. With scale=None it just resizes the whole image.
    """
    if scale is None:
        return cv2.resize(image, (out_w, out_h))

    src_h, src_w = image.shape[:2]
    x, y, box_w, box_h = bbox_xywh
    scale = min((src_h - 1) / box_h, (src_w - 1) / box_w, scale)

    new_w = box_w * scale
    new_h = box_h * scale
    cx, cy = x + box_w / 2, y + box_h / 2

    left = cx - new_w / 2
    top = cy - new_h / 2
    right = cx + new_w / 2
    bottom = cy + new_h / 2

    if left < 0:
        right -= left
        left = 0
    if top < 0:
        bottom -= top
        top = 0
    if right > src_w - 1:
        left -= right - src_w + 1
        right = src_w - 1
    if bottom > src_h - 1:
        top -= bottom - src_h + 1
        bottom = src_h - 1

    crop = image[int(top):int(bottom) + 1, int(left):int(right) + 1]
    return cv2.resize(crop, (out_w, out_h))


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


class AntiSpoofing:
    """Silent-Face MiniFASNet ensemble liveness check (CPU, onnxruntime)."""

    def __init__(self, spoof_threshold: float = None, model_dir: Path = None):
        self.spoof_threshold = (
            DEFAULT_SPOOF_THRESHOLD if spoof_threshold is None else spoof_threshold
        )
        self.min_face_size = MIN_FACE_SIZE
        self.mode = ANTI_SPOOF_MODE
        model_dir = Path(model_dir) if model_dir else MODEL_DIR

        # Each entry: (onnx session, crop scale, input_w, input_h)
        self.models: List[Tuple[ort.InferenceSession, Optional[float], int, int]] = []

        if self.mode == "disabled":
            logger.info("Anti-spoofing is DISABLED (ANTI_SPOOF_MODE=disabled).")
            return

        if not model_dir.exists():
            logger.warning(
                "Anti-spoof model dir %s not found; liveness check disabled.",
                model_dir,
            )
        else:
            for path in sorted(model_dir.glob("*.onnx")):
                try:
                    sess = ort.InferenceSession(
                        str(path), providers=["CPUExecutionProvider"]
                    )
                    shape = sess.get_inputs()[0].shape  # [1, 3, H, W]
                    in_h, in_w = int(shape[2]), int(shape[3])
                    self.models.append((sess, _parse_scale(path.name), in_w, in_h))
                except Exception as exc:  # noqa: BLE001
                    logger.error("Failed to load anti-spoof model %s: %s", path.name, exc)

        if self.models:
            logger.info(
                "Anti-spoofing ready (%d models, mode=%s, threshold=%.2f).",
                len(self.models), self.mode, self.spoof_threshold,
            )

    def is_real(self, image: np.ndarray, bbox: list, is_group: bool = False) -> Tuple[bool, float]:
        """Return (is_real, real_probability) for the face at `bbox`.

        `bbox` is [x1, y1, x2, y2] in `image` (the full original photo).
        """
        # If mode is disabled, always pass
        if self.mode == "disabled":
            return True, 1.0

        if not self.models:
            return True, 1.0

        x1, y1, x2, y2 = [int(v) for v in bbox]
        face_w, face_h = x2 - x1, y2 - y1
        if face_w <= 0 or face_h <= 0:
            return False, 0.0

        # Too small to judge — accept to avoid false rejections
        if min(face_w, face_h) < self.min_face_size:
            logger.debug(
                "Face too small for anti-spoof (%dx%d < %dpx) — accepting.",
                face_w, face_h, self.min_face_size,
            )
            return True, 1.0

        bbox_xywh = [x1, y1, face_w, face_h]
        combined = np.zeros(3, dtype=np.float64)
        for sess, scale, in_w, in_h in self.models:
            crop = _crop(image, bbox_xywh, scale, in_w, in_h)
            blob = crop.transpose(2, 0, 1).astype(np.float32)[None]  # NCHW, BGR, [0,255]
            logits = sess.run(None, {sess.get_inputs()[0].name: blob})[0]
            combined += _softmax(logits[0].astype(np.float64))

        n = len(self.models)
        real_score = float(combined[1] / n)  # class 1 == real
        is_real = real_score >= self.spoof_threshold

        logger.info(
            "Spoof check | bbox=[%d,%d,%d,%d] (%dx%d) | real=%.3f thr=%.2f | %s | mode=%s",
            x1, y1, x2, y2, face_w, face_h, real_score,
            self.spoof_threshold, "REAL" if is_real else "SPOOF", self.mode,
        )

        # In advisory mode, always return True but log the score
        if self.mode == "advisory":
            return True, real_score

        return is_real, real_score
