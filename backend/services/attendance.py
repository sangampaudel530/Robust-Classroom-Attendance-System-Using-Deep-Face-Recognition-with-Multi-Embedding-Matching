"""
services/attendance.py
Core attendance processing pipeline.
Uses shared InsightFace app (no double loading).
"""

import cv2
import numpy as np
import logging
import uuid
from datetime import date as date_type
from pathlib import Path
from typing import Dict, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.models.attendance import AttendanceRecord
from backend.models.student import Student
from backend.services.face_detector import FaceDetector
from backend.services.recognizer import FaceRecognizer, get_shared_app
from backend.services.anti_spoof import AntiSpoofing
import os
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

UPLOAD_DIR      = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.45"))
# Anti-spoof enforcement is now handled inside AntiSpoofing class 
# via the ANTI_SPOOF_MODE env var ("disabled", "advisory", "enforce").
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class AttendanceService:

    def __init__(self):
        # Both share the same loaded model — no double loading
        shared = get_shared_app()
        self.detector   = FaceDetector(app=shared)
        self.recognizer = FaceRecognizer()
        self.anti_spoof = AntiSpoofing()

    async def process_class_photo(
        self,
        image_bytes: bytes,
        class_date: date_type,
        db: AsyncSession,
    ) -> Dict:
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Invalid image file.")

        photo_path = UPLOAD_DIR / f"class_{class_date}_{uuid.uuid4().hex[:8]}.jpg"
        cv2.imwrite(str(photo_path), image)

        # All active students
        result = await db.execute(
            select(Student).where(Student.is_active == True)
        )
        students: List[Student] = result.scalars().all()
        if not students:
            return {"error": "No enrolled students found."}

        enrolled_rolls = [s.roll_no for s in students]
        student_map    = {s.roll_no: s.name for s in students}

        # Default all absent
        attendance = {
            s.roll_no: {"status": "A", "confidence": 0.0, "name": s.name}
            for s in students
        }

        detected_faces = self.detector.detect(image, is_group=True)
        faces_detected  = len(detected_faces)
        spoofs_rejected = 0

        logger.info(f"Detected {faces_detected} faces for {class_date}")

        for face_data in detected_faces:
            bbox      = face_data["bbox"]
            face_crop = face_data["face_crop"]

            # Liveness check on every detected face (mode-dependent)
            is_real, spoof_score = self.anti_spoof.is_real(image, bbox)
            if not is_real and self.anti_spoof.mode == "enforce":
                spoofs_rejected += 1
                logger.warning(f"Spoof rejected (score={spoof_score:.2f})")
                continue

            # Use pre-computed embedding from detector (which now correctly provides normed_embedding)
            embedding = face_data.get("embedding")
            if embedding is None:
                continue

            matched_roll, match_score = self.recognizer.match_against_all(
                embedding, enrolled_rolls, threshold=MATCH_THRESHOLD
            )

            if matched_roll:
                if match_score > attendance[matched_roll]["confidence"]:
                    attendance[matched_roll] = {
                        "status":     "P",
                        "confidence": round(match_score, 4),
                        "name":       student_map[matched_roll],
                    }
                    logger.info(f"  Matched {matched_roll} score={match_score:.3f}")
            else:
                # Active Learning Candidate Generation for unrecognized faces
                suggested_roll, suggested_score = self.recognizer.match_against_all(
                    embedding, enrolled_rolls, threshold=0.0
                )
                
                candidate_id = uuid.uuid4().hex[:16]
                crop_dir = Path("data/active_learning")
                crop_dir.mkdir(parents=True, exist_ok=True)
                crop_filename = f"crop_{class_date}_{candidate_id}.jpg"
                crop_path = crop_dir / crop_filename
                cv2.imwrite(str(crop_path), face_crop)
                
                emb_filename = f"emb_{class_date}_{candidate_id}.npy"
                emb_path = crop_dir / emb_filename
                np.save(str(emb_path), embedding)
                
                from backend.models.active_learning import ActiveLearningCandidate
                db.add(ActiveLearningCandidate(
                    id=candidate_id,
                    class_date=class_date,
                    face_crop_path=f"data/active_learning/{crop_filename}",
                    embedding_path=f"data/active_learning/{emb_filename}",
                    suggested_roll_no=suggested_roll,
                    suggested_confidence=round(suggested_score, 4),
                ))
                logger.info(f"Unrecognized face saved as Active Learning candidate: {candidate_id} (suggested: {suggested_roll} conf: {suggested_score:.3f})")

        # Persist to DB
        for roll_no, info in attendance.items():
            record_id = f"{roll_no}_{class_date}"
            existing  = await db.get(AttendanceRecord, record_id)
            if existing:
                existing.status           = info["status"]
                existing.confidence       = info["confidence"]
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

        present = sum(1 for v in attendance.values() if v["status"] == "P")
        return {
            "date":            str(class_date),
            "total_students":  len(students),
            "present":         present,
            "absent":          len(students) - present,
            "faces_detected":  faces_detected,
            "spoofs_rejected": spoofs_rejected,
            "details": [
                {"roll_no": roll, "name": info["name"],
                 "status": info["status"], "confidence": info["confidence"]}
                for roll, info in sorted(attendance.items())
            ],
        }

    async def get_attendance_by_date(self, class_date, db):
        result = await db.execute(
            select(AttendanceRecord)
            .where(AttendanceRecord.date == class_date)
            .order_by(AttendanceRecord.roll_no)
        )
        return [r.to_dict() for r in result.scalars().all()]

    async def get_student_attendance(self, roll_no, db):
        result = await db.execute(
            select(AttendanceRecord)
            .where(AttendanceRecord.roll_no == roll_no)
            .order_by(AttendanceRecord.date)
        )
        return [r.to_dict() for r in result.scalars().all()]