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
from backend.services.recognizer import EMBED_DIR, FaceRecognizer, get_shared_app, invalidate_gallery
from backend.services.student_validation import (
    MAX_PHOTO_BYTES,
    MAX_PHOTOS_PER_REQUEST,
    normalize_student_info,
)
from backend.services.video_processor import UPLOAD_DIR

AL_DIR = Path(os.getenv("ACTIVE_LEARNING_DIR", "data/active_learning"))
AL_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/students", tags=["students"])

PHOTO_DIR = Path(os.getenv("PHOTO_DIR") or os.getenv("STUDENT_PHOTOS_DIR") or "data/student_photos")
PHOTO_DIR.mkdir(parents=True, exist_ok=True)
MIN_PHOTOS_WARN = 3   # warn teacher if fewer photos


def _candidate_crop_path(path_value: str) -> Path:
    path = Path(path_value).resolve()
    if path.parent != AL_DIR.resolve():
        raise HTTPException(400, "Invalid active-learning crop path.")
    return path


@router.get("")
async def list_students(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Student).where(Student.is_active.is_(True)).order_by(Student.roll_no)
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
    try:
        roll_no, name = normalize_student_info(roll_no, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not photos:
        raise HTTPException(400, "At least one photo is required.")
    if len(photos) > MAX_PHOTOS_PER_REQUEST:
        raise HTTPException(400, f"Upload a maximum of {MAX_PHOTOS_PER_REQUEST} photos at a time.")

    existing = await db.get(Student, roll_no)
    if existing and existing.is_active:
        raise HTTPException(409, f"Student {roll_no} is already enrolled.")

    shared     = get_shared_app()
    detector   = FaceDetector(app=shared)
    recognizer = FaceRecognizer()

    embeddings = []
    student_dir = PHOTO_DIR / roll_no
    saved_paths: list[Path] = []
    rejected = 0

    for upload in photos:
        data = await upload.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            rejected += 1
            continue
        nparr = np.frombuffer(data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            rejected += 1
            continue

        faces = detector.detect(image, is_group=False)
        if not faces:
            rejected += 1
            continue

        face = max(faces, key=lambda f: (f["bbox"][2]-f["bbox"][0])*(f["bbox"][3]-f["bbox"][1]))
        emb  = face.get("embedding")
        if emb is None:
            emb = recognizer.get_embedding(face["face_crop"])
        if emb is not None:
            student_dir.mkdir(parents=True, exist_ok=True)
            photo_path = student_dir / f"{uuid.uuid4().hex[:8]}.jpg"
            if cv2.imwrite(str(photo_path), image):
                embeddings.append(emb)
                saved_paths.append(photo_path)
            else:
                rejected += 1
        else:
            rejected += 1

    if not embeddings:
        if student_dir.exists() and not any(student_dir.iterdir()):
            student_dir.rmdir()
        raise HTTPException(400, "No valid face detected in uploaded photos.")

    previous_embeddings = recognizer.load_embeddings(roll_no) if existing else None
    try:
        processed = recognizer.add_embeddings(roll_no, embeddings) if existing else recognizer.enroll_from_embeddings(roll_no, embeddings)

        if existing:
            existing.name = name
            existing.is_active = True
            existing.enrolled_at = datetime.utcnow()
        else:
            db.add(Student(roll_no=roll_no, name=name, enrolled_at=datetime.utcnow(), is_active=True))

        await db.commit()
    except Exception:
        await db.rollback()
        for path in saved_paths:
            path.unlink(missing_ok=True)
        if previous_embeddings is not None:
            recognizer.save_embeddings(roll_no, list(previous_embeddings))
        else:
            recognizer.remove_embedding(roll_no)
        if student_dir.exists() and not any(student_dir.iterdir()):
            student_dir.rmdir()
        raise
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
        "photos_rejected": rejected,
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

    # Stage recognition files first so a database failure can restore them.
    student_photo_dir = PHOTO_DIR / roll_no
    photos_deleted = 0
    if student_photo_dir.exists():
        photos_deleted = sum(1 for f in student_photo_dir.rglob("*") if f.is_file())
    sources = [
        student_photo_dir,
        EMBED_DIR / f"{roll_no}.npz",
        EMBED_DIR / f"{roll_no}.npy",
    ]
    staged_paths: list[tuple[Path, Path]] = []
    try:
        for source in sources:
            if source.exists():
                staged = source.with_name(f".{source.name}.{uuid.uuid4().hex}.pending-delete")
                source.rename(staged)
                staged_paths.append((source, staged))
    except OSError:
        for source, staged in reversed(staged_paths):
            if staged.exists() and not source.exists():
                staged.rename(source)
        raise HTTPException(500, "Could not stage student files for deletion.")

    att_result = await db.execute(
        select(AttendanceRecord).where(AttendanceRecord.roll_no == roll_no)
    )
    att_records    = att_result.scalars().all()
    records_deleted = len(att_records)
    class_photo_paths = {
        record.class_photo_path for record in att_records if record.class_photo_path
    }
    try:
        for rec in att_records:
            await db.delete(rec)
        await db.delete(student)
        await db.commit()
    except Exception:
        await db.rollback()
        for source, staged in reversed(staged_paths):
            if staged.exists() and not source.exists():
                staged.rename(source)
        raise

    for _, staged in staged_paths:
        if staged.is_dir():
            shutil.rmtree(staged, ignore_errors=True)
        else:
            staged.unlink(missing_ok=True)

    upload_root = UPLOAD_DIR.resolve()
    for path_value in class_photo_paths:
        still_used = await db.execute(
            select(AttendanceRecord.id)
            .where(AttendanceRecord.class_photo_path == path_value)
            .limit(1)
        )
        if still_used.scalar_one_or_none() is None:
            class_photo = Path(path_value)
            try:
                if class_photo.parent.resolve() == upload_root:
                    class_photo.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove unused class photo %s", class_photo)
    invalidate_gallery()

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
    if not student or not student.is_active:
        raise HTTPException(404, f"Student {roll_no} not found.")

    # Move the face crop into the student's official photo directory
    src = _candidate_crop_path(candidate.face_crop_path)
    if not src.exists():
        raise HTTPException(404, "Face crop image file not found on disk.")

    dest_dir = PHOTO_DIR / roll_no
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"al_{candidate_id[:8]}.jpg"
    recognizer = FaceRecognizer()
    shutil.copy2(str(src), str(dest))
    try:
        # Re-compute embeddings for this student to include the new photo.
        recognizer.update_student_embedding(roll_no)
        await db.delete(candidate)
        await db.commit()
    except Exception:
        await db.rollback()
        dest.unlink(missing_ok=True)
        recognizer.update_student_embedding(roll_no)
        raise

    src.unlink(missing_ok=True)

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

    crop = _candidate_crop_path(candidate.face_crop_path)
    await db.delete(candidate)
    await db.commit()
    crop.unlink(missing_ok=True)

    logger.info("Active learning: rejected candidate %s.", candidate_id)
    return {"message": "Candidate dismissed."}
