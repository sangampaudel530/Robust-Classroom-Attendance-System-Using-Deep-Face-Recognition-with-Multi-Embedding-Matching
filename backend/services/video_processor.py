"""
services/video_processor.py
Processes a short classroom video, extracting frames and applying multi-frame voting
for highly accurate attendance.

Key benefits of Video vs Photo:
  - Captures students from multiple angles naturally.
  - Multi-frame voting (e.g. must be seen in >= 2 frames) eliminates false positives.
  - Mitigates occlusions (people moving their heads, walking in front).
"""

import logging
import os
import uuid
from datetime import date as date_type
from pathlib import Path
from typing import Dict, List, Set, Tuple

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.active_learning import ActiveLearningCandidate
from backend.models.attendance import AttendanceRecord
from backend.models.student import Student
from backend.services.anti_spoof import AntiSpoofing
from backend.services.face_detector import FaceDetector
from backend.services.recognizer import FaceRecognizer, get_shared_app

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.55"))

# Number of frames to extract from the video clip
MAX_FRAMES_TO_EXTRACT = 10
# Minimum frames a student must be recognized in to be marked present (Voting threshold)
MIN_FRAMES_FOR_PRESENT = 2


class VideoProcessor:
    def __init__(self):
        shared = get_shared_app()
        self.detector = FaceDetector(app=shared)
        self.recognizer = FaceRecognizer()
        self.anti_spoof = AntiSpoofing()

    def extract_frames(self, video_path: str) -> List[np.ndarray]:
        """Extract uniformly distributed frames from the video."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error("Could not open video file: %s", video_path)
            return []

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            return []

        # Calculate stride to get roughly MAX_FRAMES_TO_EXTRACT
        stride = max(1, total_frames // MAX_FRAMES_TO_EXTRACT)
        
        frames = []
        frame_idx = 0
        while cap.isOpened() and len(frames) < MAX_FRAMES_TO_EXTRACT:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)
            frame_idx += stride

        cap.release()
        logger.info("Extracted %d frames from video (total_frames=%d).", len(frames), total_frames)
        return frames

    async def process_video(
        self,
        video_bytes: bytes,
        class_date: date_type,
        db: AsyncSession,
        filename_ext: str = ".mp4"
    ) -> Dict:
        """Process a video file for attendance using multi-frame voting."""
        
        video_filename = f"class_video_{class_date}_{uuid.uuid4().hex[:8]}{filename_ext}"
        video_path = UPLOAD_DIR / video_filename
        
        # Save video temporarily to extract frames
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        frames = self.extract_frames(str(video_path))
        if not frames:
            video_path.unlink()
            return {"error": "Could not extract frames from the video."}

        # Save a representative frame as the "class photo" for records
        rep_frame = frames[len(frames) // 2]
        photo_filename = f"class_{class_date}_{uuid.uuid4().hex[:8]}.jpg"
        photo_path = UPLOAD_DIR / photo_filename
        cv2.imwrite(str(photo_path), rep_frame)

        # Get active students
        result = await db.execute(select(Student).where(Student.is_active == True))
        students: List[Student] = result.scalars().all()
        if not students:
            video_path.unlink()
            return {"error": "No enrolled students found."}

        enrolled_rolls = [s.roll_no for s in students]
        student_map = {s.roll_no: s.name for s in students}

        # Track hits per student: {roll_no: list_of_confidence_scores}
        student_hits: Dict[str, List[float]] = {roll: [] for roll in enrolled_rolls}
        
        total_faces_detected = 0
        spoofs_rejected = 0

        # Process each frame
        for i, frame in enumerate(frames):
            logger.info("Processing frame %d/%d", i + 1, len(frames))
            detected_faces = self.detector.detect(frame, is_group=True)
            total_faces_detected += len(detected_faces)

            for face_data in detected_faces:
                bbox = face_data["bbox"]
                
                # Anti-spoofing check (often disabled for teacher uploads)
                is_real, spoof_score = self.anti_spoof.is_real(frame, bbox)
                if not is_real and self.anti_spoof.mode == "enforce":
                    spoofs_rejected += 1
                    continue

                emb = face_data.get("embedding")
                if emb is None:
                    continue

                matched_roll, match_score = self.recognizer.match_against_all(
                    emb, enrolled_rolls, threshold=MATCH_THRESHOLD
                )

                if matched_roll:
                    student_hits[matched_roll].append(match_score)
                    
                # Active learning candidate logic omitted for video to avoid spamming
                # the DB with 10x copies of the same unrecognized person. 
                # (Could be added with cross-frame tracking in the future).

        # Aggregate results
        attendance = {}
        for roll_no in enrolled_rolls:
            hits = student_hits[roll_no]
            if len(hits) >= MIN_FRAMES_FOR_PRESENT:
                # Marked present! Use max confidence score from their hits
                max_conf = max(hits)
                attendance[roll_no] = {
                    "status": "P",
                    "confidence": round(max_conf, 4),
                    "name": student_map[roll_no],
                    "frames_seen": len(hits)
                }
            else:
                attendance[roll_no] = {
                    "status": "A",
                    "confidence": 0.0,
                    "name": student_map[roll_no],
                    "frames_seen": len(hits)
                }

        # Persist to DB
        for roll_no, info in attendance.items():
            record_id = f"{roll_no}_{class_date}"
            existing = await db.get(AttendanceRecord, record_id)
            if existing:
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
        
        # Cleanup video (keep the representative photo)
        try:
            video_path.unlink()
        except OSError:
            pass

        present_count = sum(1 for v in attendance.values() if v["status"] == "P")
        
        return {
            "date": str(class_date),
            "total_students": len(students),
            "present": present_count,
            "absent": len(students) - present_count,
            "frames_processed": len(frames),
            "faces_detected": total_faces_detected,
            "spoofs_rejected": spoofs_rejected,
            "details": [
                {"roll_no": roll, "name": info["name"],
                 "status": info["status"], "confidence": info["confidence"],
                 "frames_seen": info["frames_seen"]}
                for roll, info in sorted(attendance.items())
            ],
        }
