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

AL_DIR = Path("data/active_learning")
AL_DIR.mkdir(parents=True, exist_ok=True)

MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.55"))

# Target FPS for extraction (Lower = Faster processing, 1 is usually enough for a 10s video)
TARGET_FPS = 1
# Minimum frames a student must be recognized in to be marked present (Voting threshold)
MIN_FRAMES_FOR_PRESENT = 2

class VideoProcessor:
    def __init__(self):
        shared = get_shared_app()
        self.detector = FaceDetector(app=shared)
        self.recognizer = FaceRecognizer()
        self.anti_spoof = AntiSpoofing()

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
            return []

        # Calculate stride to extract exactly TARGET_FPS frames per second
        stride = max(1, int(fps / TARGET_FPS))
        
        frames = []
        frame_idx = 0
        while cap.isOpened():
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)
            frame_idx += stride
            
            # Cap at some reasonable maximum to prevent memory exhaustion (e.g. 60 seconds = 120 frames)
            if len(frames) > 120:
                break

        cap.release()
        logger.info("Extracted %d frames from video (total_frames=%d, fps=%.1f).", len(frames), total_frames, fps)
        return frames

    async def process_video(
        self,
        video_bytes: bytes,
        class_date: date_type,
        db: AsyncSession,
        filename_ext: str = ".mp4"
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
            yield {"type": "error", "message": "No enrolled students found."}
            return

        enrolled_rolls = [s.roll_no for s in students]
        student_map = {s.roll_no: s.name for s in students}

        # Track data per student: {roll_no: list_of_dicts}
        student_data: Dict[str, List[Dict]] = {roll: [] for roll in enrolled_rolls}
        
        total_faces_detected = 0
        spoofs_rejected = 0

        # Process each frame
        for i, frame in enumerate(frames):
            logger.info("Processing frame %d/%d", i + 1, len(frames))
            
            # Visual display frame
            display_frame = frame.copy()
            
            # Use video_mode=True to skip slow tiled detection
            detected_faces = self.detector.detect(frame, is_group=True, video_mode=True)
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
                    # Only run the expensive anti-spoof neural network on detected faces
                    is_real, spoof_score = self.anti_spoof.is_real(frame, bbox)
                    
                    if match_score >= MATCH_THRESHOLD:
                        student_data[best_roll].append({
                            "match_score": match_score,
                            "spoof_score": spoof_score,
                            "pose": pose
                        })
                        
                        if is_real:
                            box_color = (0, 255, 0) # Green for Real
                            label = f"{student_map[best_roll]} ({match_score:.2f}) [REAL]"
                        else:
                            box_color = (0, 0, 255) # Red for Spoof
                            label = f"{student_map[best_roll]} ({match_score:.2f}) [SPOOF]"
                            
                    else:
                        # Match score is below threshold
                        if not is_real:
                            # It's a detected spoof (even though it didn't confidently match a student)
                            box_color = (0, 0, 255) # Red
                            label = f"{student_map[best_roll]} ({match_score:.2f}) [SPOOF]"
                        elif match_score >= 0.35:
                            # It passed the liveness check, but match score is low. Treat as Active Learning.
                            box_color = (0, 255, 255) # Yellow for Active Learning Candidate
                            label = f"? {student_map[best_roll]} ({match_score:.2f})"
                            
                            # ACTIVE LEARNING: High enough to be a face, low enough to fail threshold, but passed anti-spoof
                            # Only save if we have a face crop
                            if "face_crop" in face_data and face_data["face_crop"] is not None:
                                crop_id = uuid.uuid4().hex
                                crop_filename = f"{crop_id}.jpg"
                                crop_path = AL_DIR / crop_filename
                                cv2.imwrite(str(crop_path), face_data["face_crop"])
                                
                                db.add(ActiveLearningCandidate(
                                    id=crop_id,
                                    class_date=class_date,
                                    face_crop_path=f"data/active_learning/{crop_filename}",
                                    suggested_roll_no=best_roll,
                                    suggested_name=student_map[best_roll],
                                    suggested_confidence=match_score
                                ))

                if bbox is not None:
                    x1, y1, x2, y2 = map(int, bbox[:4])
                    cv2.rectangle(display_frame, (x1, y1), (x2, y2), box_color, 2)
                    cv2.putText(display_frame, label, (x1, max(y1-10, 10)), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.7, box_color, 2)

            # Yield the frame for live web streaming
            import base64
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
                # 1. Evaluate Temporal Anti-Spoofing
                avg_spoof_score = sum(d["spoof_score"] for d in data) / len(data)
                
                # Check pose variance (Pitch, Yaw, Roll)
                poses = [d["pose"] for d in data if d["pose"] is not None]
                is_static_spoof = False
                
                if len(poses) >= 2 and self.anti_spoof.mode == "enforce":
                    pose_arr = np.array(poses) # shape: (N, 3)
                    variances = np.var(pose_arr, axis=0)
                    total_variance = np.sum(variances)
                    logger.debug(f"{roll_no} Pose Variance: {total_variance}")
                    
                    # If variance is highly unnatural (too static), mark as spoof
                    if total_variance < 0.5:
                        is_static_spoof = True
                        logger.warning(f"{roll_no} rejected due to low 3D pose variance (Static Photo Spoof).")

                if (avg_spoof_score < self.anti_spoof.spoof_threshold and self.anti_spoof.mode == "enforce") or is_static_spoof:
                    spoofs_rejected += 1
                    attendance[roll_no] = {
                        "status": "A",
                        "confidence": 0.0,
                        "name": student_map[roll_no],
                        "frames_seen": len(data)
                    }
                else:
                    # Passed Liveness Check
                    max_conf = max(d["match_score"] for d in data)
                    attendance[roll_no] = {
                        "status": "P",
                        "confidence": round(max_conf, 4),
                        "name": student_map[roll_no],
                        "frames_seen": len(data)
                    }
            else:
                attendance[roll_no] = {
                    "status": "A",
                    "confidence": 0.0,
                    "name": student_map[roll_no],
                    "frames_seen": len(data)
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
        
        final_result = {
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
        
        yield {
            "type": "result",
            "data": final_result
        }
