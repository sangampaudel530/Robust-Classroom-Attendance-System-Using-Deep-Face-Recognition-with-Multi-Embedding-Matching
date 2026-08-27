"""
services/face_detector.py
Face detection and preprocessing using InsightFace with multi-scale tiled detection.

Key improvements over v1:
  - Tiled detection: splits large group photos into overlapping tiles so small/distant
    faces are detected at full resolution.
  - NMS merging: de-duplicates detections across tile boundaries.
  - Multi-scale: dynamically chooses det_size based on image dimensions.
  - CLAHE fallback: if no faces detected on the original, retries with contrast enhancement.
  - Proper embedding extraction: uses normed_embedding from InsightFace detection results
    to avoid the expensive re-detection-on-crops approach.
"""

import logging
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Minimum face size (pixels, min side of bbox) to accept
MIN_FACE_PX = 30


class FaceDetector:
    def __init__(self, app=None):
        if app is None:
            from backend.services.recognizer import get_shared_app
            app = get_shared_app()
        self.app = app
        import onnxruntime as ort
        self._ctx_id = 0 if "CUDAExecutionProvider" in ort.get_available_providers() else -1
        self._last_det_size = None
        logger.info("FaceDetector ctx_id=%d (%s)", self._ctx_id, "GPU" if self._ctx_id == 0 else "CPU")

    # -- Preprocessing -------------------------------------------------------

    @staticmethod
    def preprocess(image: np.ndarray) -> np.ndarray:
        """CLAHE contrast enhancement for low-light images."""
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    # -- Non-Maximum Suppression for merging tiled detections -----------------

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float = 0.4) -> List[int]:
        """Standard NMS. boxes shape (N, 4) as [x1,y1,x2,y2], scores shape (N,)."""
        if len(boxes) == 0:
            return []

        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 2]
        y2 = boxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)

        order = scores.argsort()[::-1]
        keep = []

        while order.size > 0:
            i = order[0]
            keep.append(int(i))

            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            w = np.maximum(0, xx2 - xx1)
            h = np.maximum(0, yy2 - yy1)
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)

            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]

        return keep

    # -- Tiled detection for group photos ------------------------------------

    def _detect_single_pass(self, image: np.ndarray, det_size: tuple) -> list:
        """Run only detection and retain inputs needed for later recognition.

        Recognition is deferred until after cross-pass NMS so duplicate faces
        from full-image and tiled passes are not embedded unnecessarily.
        """
        current_size = tuple(self.app.det_model.input_size or ())
        if self._last_det_size != det_size or current_size != det_size:
            # Only the detector input changes between passes. Calling the
            # FaceAnalysis-level prepare method also loops over other models.
            self.app.det_model.prepare(
                self._ctx_id,
                input_size=det_size,
                det_thresh=self.app.det_thresh,
            )
            self.app.det_size = det_size
            self._last_det_size = det_size
        bboxes, keypoints = self.app.det_model.detect(image)
        return [
            {
                "bbox": row[:4].astype(float),
                "score": float(row[4]),
                "kps": keypoints[index].copy() if keypoints is not None else None,
                "embedding_image": image,
            }
            for index, row in enumerate(bboxes)
        ]

    def _get_embedding(self, detection: Dict[str, Any]) -> Optional[np.ndarray]:
        """Calculate ArcFace once for a detection that survived final NMS."""
        keypoints = detection.get("kps")
        source_image = detection.get("embedding_image")
        recognition_model = self.app.models.get("recognition")
        if keypoints is None or source_image is None or recognition_model is None:
            return None

        from insightface.app.common import Face

        face = Face(
            bbox=np.asarray(detection["bbox"], dtype=np.float32),
            kps=np.asarray(keypoints, dtype=np.float32),
            det_score=float(detection.get("score", 0.5)),
        )
        recognition_model.get(source_image, face)
        return getattr(face, "normed_embedding", None)

    def _detect_tiled(self, image: np.ndarray, tile_overlap: float = 0.25) -> list:
        """Detect faces using overlapping tiles for large group images.

        Splits the image into tiles, runs detection on each at high resolution,
        maps bounding boxes back to original coordinates, then merges with NMS.
        """
        h, w = image.shape[:2]

        # Determine tiling grid based on image size
        if max(w, h) <= 1280:
            # Small enough for single-pass
            return self._detect_single_pass(image, (1280, 1280))

        # Calculate tile count: aim for ~800-1000px per tile with overlap
        tile_size_target = 960
        cols = max(1, int(np.ceil(w / (tile_size_target * (1 - tile_overlap)))))
        rows = max(1, int(np.ceil(h / (tile_size_target * (1 - tile_overlap)))))

        # Cap at reasonable tiling
        cols = min(cols, 4)
        rows = min(rows, 4)

        step_x = w / cols if cols > 1 else w
        step_y = h / rows if rows > 1 else h

        tile_w = int(step_x + w * tile_overlap) if cols > 1 else w
        tile_h = int(step_y + h * tile_overlap) if rows > 1 else h

        all_boxes = []
        all_scores = []
        all_faces_data = []  # Store complete face data for each detection

        det_size = (640, 640)  # Detection resolution per tile

        for row in range(rows):
            for col in range(cols):
                x_start = int(col * step_x)
                y_start = int(row * step_y)
                x_end = min(x_start + tile_w, w)
                y_end = min(y_start + tile_h, h)

                tile = image[y_start:y_end, x_start:x_end]
                if tile.shape[0] < 50 or tile.shape[1] < 50:
                    continue

                faces = self._detect_single_pass(tile, det_size)

                for face in faces:
                    # Map bbox back to original image coordinates
                    bbox = face["bbox"].copy()
                    bbox[0] += x_start
                    bbox[1] += y_start
                    bbox[2] += x_start
                    bbox[3] += y_start

                    score = face["score"]

                    all_boxes.append(bbox)
                    all_scores.append(score)

                    all_faces_data.append({
                        "bbox": bbox,
                        "score": score,
                        # Keep tile-local landmarks/source pixels so the aligned
                        # recognition crop matches the previous implementation.
                        "kps": face["kps"],
                        "embedding_image": tile,
                    })

        if not all_boxes:
            return []

        # NMS to remove duplicates at tile boundaries
        boxes_arr = np.array(all_boxes, dtype=np.float32)
        scores_arr = np.array(all_scores, dtype=np.float32)
        keep_indices = self._nms(boxes_arr, scores_arr, iou_threshold=0.4)

        # Build mock face objects with the kept detections
        kept_faces = [all_faces_data[i] for i in keep_indices]
        return kept_faces

    # -- Also do a full-image pass (catches faces that straddle tile boundaries)

    def _detect_full_image(self, image: np.ndarray) -> list:
        """Full-image detection at the highest reasonable det_size."""
        h, w = image.shape[:2]
        max_dim = max(w, h)
        if max_dim > 1920:
            det_size = (1920, 1920)
        elif max_dim > 1280:
            det_size = (1280, 1280)
        else:
            det_size = (640, 640)
        return self._detect_single_pass(image, det_size)

    # -- Main detection entry point ------------------------------------------

    def detect(
        self,
        image: np.ndarray,
        is_group: bool = False,
        use_clahe_fallback: bool = True,
        video_mode: bool = False,
    ) -> List[Dict[str, Any]]:
        """Detect all faces in an image.

        For group photos (is_group=True), uses a combined strategy:
          1. Full-image detection at high det_size
          2. Tiled detection at higher per-tile resolution (catches small faces)
          3. NMS to merge both sets of detections
          
        If video_mode=True, tiled detection is skipped to significantly improve processing speed.

        Returns list of dicts with 'bbox', 'face_crop', 'embedding'.
        """
        h, w = image.shape[:2]
        raw_detections = []

        if is_group and max(w, h) > 1000:
            # Strategy 1: Full image pass
            full_faces = self._detect_full_image(image)
            for face in full_faces:
                raw_detections.append({
                    "bbox": face["bbox"],
                    "score": face["score"],
                    "kps": face["kps"],
                    "embedding_image": face["embedding_image"],
                })

            # Strategy 2: Tiled detection (catches missed small faces)
            if not video_mode:
                tiled_faces = self._detect_tiled(image, tile_overlap=0.25)
                for face_data in tiled_faces:
                    raw_detections.append({
                        "bbox": face_data["bbox"].astype(float) if isinstance(face_data["bbox"], np.ndarray) else np.array(face_data["bbox"], dtype=float),
                        "score": 0.5,
                        "kps": face_data["kps"],
                        "embedding_image": face_data["embedding_image"],
                    })

            # Merge with NMS
            if raw_detections:
                all_boxes = np.array([d["bbox"] for d in raw_detections], dtype=np.float32)
                all_scores = np.array([d["score"] for d in raw_detections], dtype=np.float32)
                keep = self._nms(all_boxes, all_scores, iou_threshold=0.4)
                raw_detections = [raw_detections[i] for i in keep]

        else:
            # Single-person or small image: simple single-pass
            det_size = (640, 640)
            faces = self._detect_single_pass(image, det_size)
            for face in faces:
                raw_detections.append({
                    "bbox": face["bbox"],
                    "score": face["score"],
                    "kps": face["kps"],
                    "embedding_image": face["embedding_image"],
                })

        is_clahe_used = False

        # CLAHE fallback if nothing detected
        if not raw_detections and use_clahe_fallback:
            logger.info("No faces detected. Running CLAHE fallback...")
            enhanced = self.preprocess(image)
            det_size = (1280, 1280) if is_group else (640, 640)
            faces = self._detect_single_pass(enhanced, det_size)
            is_clahe_used = True
            for face in faces:
                raw_detections.append({
                    "bbox": face["bbox"],
                    "score": face["score"],
                    "kps": face["kps"],
                    "embedding_image": enhanced,
                })

        # Build final results with face crops.
        results: List[Dict[str, Any]] = []
        for det in raw_detections:
            bbox = det["bbox"]
            x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)

            if x2 <= x1 or y2 <= y1:
                continue

            face_w = x2 - x1
            face_h = y2 - y1
            if min(face_w, face_h) < MIN_FACE_PX:
                continue

            # Always crop from ORIGINAL image
            face_crop = image[y1:y2, x1:x2]

            # Preserve prior behavior for CLAHE: do not recognize from pixels
            # whose contrast was altered. Otherwise embed only final NMS faces.
            embedding = self._get_embedding(det) if not is_clahe_used else None

            results.append({
                "bbox": [x1, y1, x2, y2],
                "face_crop": face_crop,
                "embedding": embedding,
            })

        logger.info("Detected %d faces (group=%s, clahe=%s).", len(results), is_group, is_clahe_used)
        return results
