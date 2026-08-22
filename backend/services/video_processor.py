"""
services/video_processor.py
Processes a short classroom video, extracting frames and applying multi-frame voting
for highly accurate attendance.

Key benefits of Video vs Photo:
  - Captures students from multiple angles naturally.
  - Multi-frame voting (e.g. must be seen in >= 2 frames) eliminates false positives.
  - Mitigates occlusions (people moving their heads, walking in front).
"""

import base64
import logging
import os
import uuid
from datetime import date as date_type
from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.active_learning import ActiveLearningCandidate
from backend.models.attendance import AttendanceRecord
from backend.models.student import Student
from backend.services.face_detector import FaceDetector
from backend.services.recognizer import FaceRecognizer, get_shared_app

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

AL_DIR = Path(os.getenv("ACTIVE_LEARNING_DIR", "data/active_learning"))
AL_DIR.mkdir(parents=True, exist_ok=True)

MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.50"))

# Target FPS for extraction — GPU allows higher sampling for better accuracy
# 3 fps on a 10s video = ~30 frames, capturing students from many angles
TARGET_FPS = max(1, int(os.getenv("VIDEO_TARGET_FPS", "3")))
# Minimum frames a student must appear in to be marked present.
# With 3 fps sampling, a student visible for just 1 second hits this threshold.
MIN_FRAMES_FOR_PRESENT = max(1, int(os.getenv("VIDEO_MIN_FRAMES", "2")))
MAX_VIDEO_FRAMES = max(1, int(os.getenv("VIDEO_MAX_FRAMES", "60")))
MAX_FRAME_DIMENSION = max(640, int(os.getenv("VIDEO_MAX_FRAME_DIMENSION", "1920")))

class VideoProcessor:
    def __init__(self):
        shared = get_shared_app()
        self.detector = FaceDetector(app=shared)
        self.recognizer = FaceRecognizer()
        import onnxruntime as ort
        self._use_gpu = "CUDAExecutionProvider" in ort.get_available_providers()
        logger.info("VideoProcessor GPU=%s", self._use_gpu)

    def extract_frames(self, video_path: str) -> List[np.ndarray]:
        """Extract frames at a rate of TARGET_FPS."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error("Could not open video file: %s", video_path)
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0
            
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return []

        # Calculate stride to extract exactly TARGET_FPS frames per second
        stride = max(1, int(fps / TARGET_FPS))
        
        frames = []
        frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % stride == 0:
                height, width = frame.shape[:2]
                max_dimension = max(height, width)
                if max_dimension > MAX_FRAME_DIMENSION:
                    scale = MAX_FRAME_DIMENSION / max_dimension
                    frame = cv2.resize(
                        frame,
                        (int(width * scale), int(height * scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                frames.append(frame)
                if len(frames) >= MAX_VIDEO_FRAMES:
                    break
            frame_idx += 1

        cap.release()
        logger.info("Extracted %d frames from video (total_frames=%d, fps=%.1f).", len(frames), total_frames, fps)
        return frames

    async def process_video(
        self,
        video_bytes: bytes,
        class_date: date_type,
        db: AsyncSession,
        filename_ext: str = ".mp4",
        persist_records: bool = True,
        create_active_learning: bool = True,
    ):
        """Process a video file for attendance using multi-frame voting."""
        
        video_filename = f"class_video_{class_date}_{uuid.uuid4().hex[:8]}{filename_ext}"
        video_path = UPLOAD_DIR / video_filename
        
        # Save video temporarily to extract frames
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        frames = self.extract_frames(str(video_path))
        if not frames:
            video_path.unlink()
            yield {"type": "error", "message": "Could not extract frames from the video."}
            return

        # Get active students
        result = await db.execute(select(Student).where(Student.is_active.is_(True)))
        students: List[Student] = result.scalars().all()
        if not students:
            video_path.unlink()
            yield {"type": "error", "message": "No enrolled students found."}
            return

        # Evaluations must not create attendance artifacts.
        photo_path = None
        if persist_records:
            rep_frame = frames[len(frames) // 2]
            photo_filename = f"class_{class_date}_{uuid.uuid4().hex[:8]}.jpg"
            photo_path = UPLOAD_DIR / photo_filename
            if not cv2.imwrite(str(photo_path), rep_frame):
                video_path.unlink(missing_ok=True)
                yield {"type": "error", "message": "Could not save the representative class frame."}
                return

        enrolled_rolls = [s.roll_no for s in students]
        student_map = {s.roll_no: s.name for s in students}

        # Track data per student: {roll_no: list_of_dicts}
        student_data: Dict[str, List[Dict]] = {roll: [] for roll in enrolled_rolls}
        
        total_faces_detected = 0
        
        al_saved_rolls = set()  # prevent spamming active learning candidates for the same student

        # Process each frame
        for i, frame in enumerate(frames):
            logger.info("Processing frame %d/%d", i + 1, len(frames))
            
            # Visual display frame
            display_frame = frame.copy()
            
            # Use tiled detection only when GPU is available; skip on CPU for speed
            detected_faces = self.detector.detect(frame, is_group=True, video_mode=not self._use_gpu)
            total_faces_detected += len(detected_faces)

            for face_data in detected_faces:
                bbox = face_data["bbox"]
                pose = face_data.get("pose")
                emb = face_data.get("embedding")
                
                if emb is None:
                    if bbox is not None:
                        x1, y1, x2, y2 = map(int, bbox[:4])
                        cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                    continue

                best_roll, match_score = self.recognizer.match_against_all(
                    emb, enrolled_rolls, threshold=0.0
                )

                box_color = (255, 0, 0) # Default Blue for Unknown
                label = "Unknown"

                if best_roll:
                    if match_score >= MATCH_THRESHOLD:
                        student_data[best_roll].append({
                            "match_score": match_score,
                            "pose": pose
                        })
                        
                        box_color = (0, 255, 0) # Green for Real
                        label = f"{student_map[best_roll]} ({match_score:.2f})"
                            
                    elif match_score >= 0.35:
                        # Match score is low but above noise floor. Treat as Active Learning.
                        box_color = (0, 255, 255) # Yellow for Active Learning Candidate
                        label = f"? {student_map[best_roll]} ({match_score:.2f})"
                        
                        # Only save one AL candidate per student per video to avoid spam
                        if create_active_learning and best_roll not in al_saved_rolls and "face_crop" in face_data and face_data["face_crop"] is not None:
                            crop_id = uuid.uuid4().hex
                            crop_filename = f"{crop_id}.jpg"
                            crop_path = AL_DIR / crop_filename
                            if cv2.imwrite(str(crop_path), face_data["face_crop"]):
                                db.add(ActiveLearningCandidate(
                                    id=crop_id,
                                    class_date=class_date,
                                    face_crop_path=str(crop_path),
                                    suggested_roll_no=best_roll,
                                    suggested_name=student_map[best_roll],
                                    suggested_confidence=match_score,
                                ))
                                al_saved_rolls.add(best_roll)

                if bbox is not None:
                    x1, y1, x2, y2 = map(int, bbox[:4])
                    cv2.rectangle(display_frame, (x1, y1), (x2, y2), box_color, 2)
                    cv2.putText(display_frame, label, (x1, max(y1-10, 10)), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.7, box_color, 2)

            # Yield the frame for live web streaming
            _, buffer = cv2.imencode('.jpg', display_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            b64_img = base64.b64encode(buffer).decode('utf-8')
            yield {
                "type": "frame",
                "image": b64_img,
                "progress": int((i + 1) / len(frames) * 100)
            }

        # Aggregate results
        attendance = {}
        for roll_no in enrolled_rolls:
            data = student_data[roll_no]
            if len(data) >= MIN_FRAMES_FOR_PRESENT:
                max_conf = max(d["match_score"] for d in data)
                attendance[roll_no] = {
                    "status": "P",
                    "confidence": round(max_conf, 4),
                    "name": student_map[roll_no],
                    "frames_seen": len(data),
                }
            else:
                attendance[roll_no] = {
                    "status": "A",
                    "confidence": 0.0,
                    "name": student_map[roll_no],
                    "frames_seen": len(data)
                }

        if persist_records:
            old_photo_paths = set()
            for roll_no, info in attendance.items():
                record_id = f"{roll_no}_{class_date}"
                existing = await db.get(AttendanceRecord, record_id)
                if existing:
                    if existing.class_photo_path and existing.class_photo_path != str(photo_path):
                        old_photo_paths.add(existing.class_photo_path)
                    existing.status = info["status"]
                    existing.confidence = info["confidence"]
                    existing.class_photo_path = str(photo_path)
                else:
                    db.add(AttendanceRecord(
                        id=record_id,
                        roll_no=roll_no,
                        date=class_date,
                        status=info["status"],
                        confidence=info["confidence"],
                        class_photo_path=str(photo_path),
                    ))

            await db.commit()

            # Remove superseded class frames only when no record still uses them.
            for old_path_value in old_photo_paths:
                still_used = await db.execute(
                    select(AttendanceRecord.id)
                    .where(AttendanceRecord.class_photo_path == old_path_value)
                    .limit(1)
                )
                if still_used.scalar_one_or_none() is None:
                    old_path = Path(old_path_value)
                    if old_path.parent.resolve() == UPLOAD_DIR.resolve():
                        old_path.unlink(missing_ok=True)
        
        # Cleanup video (keep the representative photo)
        video_path.unlink(missing_ok=True)

        present_count = sum(1 for v in attendance.values() if v["status"] == "P")
        
        final_result = {
            "date": str(class_date),
            "total_students": len(students),
            "present": present_count,
            "absent": len(students) - present_count,
            "frames_processed": len(frames),
            "faces_detected": total_faces_detected,
            "details": [
                {"roll_no": roll, "name": info["name"],
                 "status": info["status"], "confidence": info["confidence"],
                 "frames_seen": info["frames_seen"]}
                for roll, info in sorted(attendance.items())
            ],
        }
        
        yield {
            "type": "result",
            "data": final_result
        }
