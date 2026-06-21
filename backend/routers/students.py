"""
routers/students.py  [IMPROVED]

Key improvements:
  - Enrollment warns if < 3 photos (poor quality)
  - Per-student enrollment quality reported (photos count + quality tier)
  - Active Learning confirm rebuilds per-photo embeddings correctly
  - All existing endpoints unchanged in interface
"""

import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.active_learning import ActiveLearningCandidate
from backend.models.attendance import AttendanceRecord
from backend.models.student import Student
from backend.services.face_detector import FaceDetector
from backend.services.recognizer import FaceRecognizer, get_shared_app, invalidate_gallery

AL_DIR = Path("data/active_learning")
AL_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/students", tags=["students"])

PHOTO_DIR = Path(os.getenv("PHOTO_DIR") or os.getenv("STUDENT_PHOTOS_DIR") or "data/student_photos")
PHOTO_DIR.mkdir(parents=True, exist_ok=True)
MIN_PHOTOS_WARN = 3   # warn teacher if fewer photos


@router.get("")
async def list_students(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Student).where(Student.is_active == True).order_by(Student.roll_no)
    )
    students = result.scalars().all()
    recognizer = FaceRecognizer()
    out = []
    for s in students:
        d = s.to_dict()
        q = recognizer.enrollment_quality(s.roll_no)
        d["enrollment_photos"] = q["photos"]
        d["enrollment_quality"] = q["quality"]
        out.append(d)
    return {"students": out, "total": len(out)}


@router.post("/enroll")
async def enroll_student(
    roll_no: str = Form(...),
    name:    str = Form(...),
    photos:  List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    roll_no = roll_no.strip()
    name    = name.strip()
    if not roll_no or not name:
        raise HTTPException(400, "Roll number and name are required.")
    if not photos:
        raise HTTPException(400, "At least one photo is required.")

    existing = await db.get(Student, roll_no)
    if existing and existing.is_active:
        raise HTTPException(409, f"Student {roll_no} is already enrolled.")

    shared     = get_shared_app()
    detector   = FaceDetector(app=shared)
    recognizer = FaceRecognizer()

    embeddings = []
    student_dir = PHOTO_DIR / roll_no
    student_dir.mkdir(parents=True, exist_ok=True)

    for upload in photos:
        data  = await upload.read()
        nparr = np.frombuffer(data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            continue

        faces = detector.detect(image, is_group=False)
        if not faces:
            continue

        face = max(faces, key=lambda f: (f["bbox"][2]-f["bbox"][0])*(f["bbox"][3]-f["bbox"][1]))
        emb  = face.get("embedding")
        if emb is None:
            emb = recognizer.get_embedding(face["face_crop"])
        if emb is not None:
            embeddings.append(emb)
            photo_path = student_dir / f"{uuid.uuid4().hex[:8]}.jpg"
            cv2.imwrite(str(photo_path), image)

    if not embeddings:
        raise HTTPException(400, "No valid face detected in uploaded photos.")

    processed = recognizer.add_embeddings(roll_no, embeddings) if existing else recognizer.enroll_from_embeddings(roll_no, embeddings)
    # The recognizer handles gallery invalidation internally now.

    if existing:
        existing.name       = name
        existing.is_active  = True
        existing.enrolled_at = datetime.utcnow()
    else:
        db.add(Student(roll_no=roll_no, name=name, enrolled_at=datetime.utcnow(), is_active=True))

    await db.commit()
    logger.info("Enrolled student %s (%d photos)", roll_no, processed)

    quality = recognizer.enrollment_quality(roll_no)["quality"]
    warning = (
        f"Only {processed} photo(s) processed this time. For best accuracy, provide at least 5 photos "
        "(front, slight left, slight right). Re-enroll with more photos anytime."
        if processed < MIN_PHOTOS_WARN else None
    )
    return {
        "roll_no":          roll_no,
        "name":             name,
        "photos_processed": processed,
        "enrollment_quality": quality,
        "warning":          warning,
    }


@router.delete("/{roll_no}")
async def remove_student(
    roll_no: str,
    keep_history: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    student = await db.get(Student, roll_no)
    if not student:
        raise HTTPException(404, f"Student {roll_no} not found.")
    if not student.is_active:
        raise HTTPException(404, f"Student {roll_no} is already inactive.")

    recognizer = FaceRecognizer()

    if keep_history:
        student.is_active = False
        await db.commit()
        # Do not remove embeddings for soft delete so they can still be recognized (optional)
        # recognizer.remove_embedding(roll_no)
        # invalidate_gallery()
        return {
            "roll_no": roll_no,
            "mode":    "soft_delete",
            "message": f"Student {roll_no} removed from roster. History preserved.",
        }

    # Hard delete
    student_photo_dir = PHOTO_DIR / roll_no
    photos_deleted = 0
    if student_photo_dir.exists():
        photos_deleted = sum(1 for f in student_photo_dir.rglob("*") if f.is_file())
        shutil.rmtree(str(student_photo_dir), ignore_errors=True)

    recognizer.remove_embedding(roll_no)
    # Gallery is invalidated by recognizer automatically now

    att_result = await db.execute(
        select(AttendanceRecord).where(AttendanceRecord.roll_no == roll_no)
    )
    att_records    = att_result.scalars().all()
    records_deleted = len(att_records)
    for rec in att_records:
        await db.delete(rec)

    await db.delete(student)
    await db.commit()

    return {
        "roll_no":                    roll_no,
        "mode":                       "hard_delete",
        "message":                    f"Student {roll_no} permanently deleted.",
        "photos_deleted":             photos_deleted,
        "attendance_records_deleted": records_deleted,
    }


# ── Active Learning ────────────────────────────────────────────────────────

@router.get("/active-learning/candidates")
async def get_active_learning_candidates(db: AsyncSession = Depends(get_db)):
    """Return all pending unrecognized face candidates."""
    result = await db.execute(
        select(ActiveLearningCandidate).order_by(ActiveLearningCandidate.created_at.desc())
    )
    candidates = result.scalars().all()
    return {"candidates": [c.to_dict() for c in candidates]}


@router.post("/active-learning/confirm")
async def confirm_active_learning_candidate(
    candidate_id: str = Form(...),
    roll_no:       str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Assign an unrecognized face to a student, add it to their gallery, and delete the candidate."""
    candidate = await db.get(ActiveLearningCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")

    student = await db.get(Student, roll_no)
    if not student:
        raise HTTPException(404, f"Student {roll_no} not found.")

    # Move the face crop into the student's official photo directory
    src = Path(candidate.face_crop_path)
    if not src.exists():
        raise HTTPException(404, "Face crop image file not found on disk.")

    dest_dir = PHOTO_DIR / roll_no
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"al_{candidate_id[:8]}.jpg"
    shutil.copy2(str(src), str(dest))
    src.unlink(missing_ok=True)  # clean up AL file

    # Re-compute embeddings for this student to include the new photo
    recognizer = FaceRecognizer()
    recognizer.update_student_embedding(roll_no)
    invalidate_gallery()

    # Delete the candidate record
    await db.delete(candidate)
    await db.commit()

    logger.info("Active learning: confirmed candidate %s as student %s.", candidate_id, roll_no)
    return {"message": f"Face confirmed as {student.name} and model updated."}


@router.post("/active-learning/reject")
async def reject_active_learning_candidate(
    candidate_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Dismiss an unrecognized face candidate without training."""
    candidate = await db.get(ActiveLearningCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")

    # Delete the face crop file
    crop = Path(candidate.face_crop_path)
    if crop.exists():
        crop.unlink(missing_ok=True)

    await db.delete(candidate)
    await db.commit()

    logger.info("Active learning: rejected candidate %s.", candidate_id)
    return {"message": "Candidate dismissed."}