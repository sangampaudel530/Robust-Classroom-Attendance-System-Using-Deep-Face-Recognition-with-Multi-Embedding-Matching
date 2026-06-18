"""
routers/students.py
Student enrollment and management endpoints.
"""

import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.student import Student
from backend.models.attendance import AttendanceRecord
from backend.models.active_learning import ActiveLearningCandidate
from backend.services.face_detector import FaceDetector
from backend.services.recognizer import FaceRecognizer, get_shared_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/students", tags=["students"])

PHOTO_DIR = Path(os.getenv("PHOTO_DIR") or os.getenv("STUDENT_PHOTOS_DIR") or "data/student_photos")
PHOTO_DIR.mkdir(parents=True, exist_ok=True)


@router.get("")
async def list_students(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Student).where(Student.is_active == True).order_by(Student.roll_no)
    )
    students = result.scalars().all()
    return {
        "students": [s.to_dict() for s in students],
        "total": len(students),
    }


@router.post("/enroll")
async def enroll_student(
    roll_no: str = Form(...),
    name: str = Form(...),
    photos: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    roll_no = roll_no.strip()
    name = name.strip()
    if not roll_no or not name:
        raise HTTPException(400, "Roll number and name are required.")
    if not photos:
        raise HTTPException(400, "At least one photo is required.")

    existing = await db.get(Student, roll_no)
    if existing and existing.is_active:
        raise HTTPException(409, f"Student {roll_no} is already enrolled.")

    shared = get_shared_app()
    detector = FaceDetector(app=shared)
    recognizer = FaceRecognizer()

    embeddings = []
    student_dir = PHOTO_DIR / roll_no
    student_dir.mkdir(parents=True, exist_ok=True)

    for upload in photos:
        data = await upload.read()
        nparr = np.frombuffer(data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            continue

        # Detect faces directly on the original image to preserve native lighting & textures
        faces = detector.detect(image, is_group=False)
        if not faces:
            continue

        face = max(
            faces,
            key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]),
        )
        embedding = face.get("embedding")
        if embedding is None:
            embedding = recognizer.get_embedding(face["face_crop"])
        if embedding is not None:
            embeddings.append(embedding)

        photo_path = student_dir / f"{uuid.uuid4().hex[:8]}.jpg"
        cv2.imwrite(str(photo_path), image)

    processed = recognizer.enroll_from_embeddings(roll_no, embeddings)
    if processed == 0:
        raise HTTPException(400, "No valid face detected in uploaded photos.")

    if existing:
        existing.name = name
        existing.is_active = True
        existing.enrolled_at = datetime.utcnow()
    else:
        db.add(Student(roll_no=roll_no, name=name, enrolled_at=datetime.utcnow(), is_active=True))

    await db.commit()
    logger.info("Enrolled student %s (%d photos)", roll_no, processed)

    return {
        "roll_no": roll_no,
        "name": name,
        "photos_processed": processed,
    }


@router.delete("/{roll_no}")
async def remove_student(
    roll_no: str,
    keep_history: bool = Query(
        False,
        description="If true, preserve attendance records (soft delete). "
                    "If false (default), permanently delete all student data."
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Remove a student from the system.

    Behaviour:
    - keep_history=false (default — hard delete):
        * Deletes all face photos from disk (data/student_photos/{roll_no}/)
        * Deletes face embedding file (data/embeddings/{roll_no}.npy)
        * Deletes all attendance records for this student from the DB
        * Deletes any Active Learning candidates suggested for this student
        * Deletes the Student row from the DB
    - keep_history=true (soft delete / roster removal):
        * Marks student as inactive (is_active=False) — hidden from UI
        * Keeps attendance records, photos, and embedding intact
        * Student can be re-enrolled later and history will be restored
    """
    student = await db.get(Student, roll_no)
    if not student:
        raise HTTPException(404, f"Student {roll_no} not found.")
    if not student.is_active:
        raise HTTPException(404, f"Student {roll_no} is already inactive/removed.")

    recognizer = FaceRecognizer()

    if keep_history:
        # ── SOFT DELETE: hide from roster, keep all data ─────────────────
        student.is_active = False
        await db.commit()
        recognizer.remove_embedding(roll_no)
        logger.info("Soft-deleted student %s (history preserved).", roll_no)
        return {
            "roll_no": roll_no,
            "mode": "soft_delete",
            "message": f"Student {roll_no} removed from roster. Attendance history preserved.",
        }

    # ── HARD DELETE: wipe everything ─────────────────────────────────────

    # 1. Delete face photos directory from disk
    student_photo_dir = PHOTO_DIR / roll_no
    photos_deleted = 0
    if student_photo_dir.exists():
        photos_deleted = sum(1 for f in student_photo_dir.rglob("*") if f.is_file())
        shutil.rmtree(str(student_photo_dir), ignore_errors=True)
        logger.info("Deleted photo directory %s (%d files).", student_photo_dir, photos_deleted)

    # 2. Delete face embedding file
    recognizer.remove_embedding(roll_no)

    # 3. Delete all Active Learning candidates suggested for this student
    al_result = await db.execute(
        select(ActiveLearningCandidate).where(
            ActiveLearningCandidate.suggested_roll_no == roll_no
        )
    )
    al_candidates = al_result.scalars().all()
    for cand in al_candidates:
        for fpath in (cand.face_crop_path, cand.embedding_path):
            p = Path(fpath)
            if p.exists():
                p.unlink(missing_ok=True)
        await db.delete(cand)

    # 4. Delete all attendance records for this student
    att_result = await db.execute(
        select(AttendanceRecord).where(AttendanceRecord.roll_no == roll_no)
    )
    att_records = att_result.scalars().all()
    records_deleted = len(att_records)
    for rec in att_records:
        await db.delete(rec)

    # 5. Hard-delete the Student row itself
    await db.delete(student)
    await db.commit()

    logger.info(
        "Hard-deleted student %s — photos: %d, attendance records: %d, AL candidates: %d.",
        roll_no, photos_deleted, records_deleted, len(al_candidates),
    )
    return {
        "roll_no": roll_no,
        "mode": "hard_delete",
        "message": f"Student {roll_no} permanently deleted.",
        "photos_deleted": photos_deleted,
        "attendance_records_deleted": records_deleted,
        "al_candidates_deleted": len(al_candidates),
    }


@router.get("/active-learning/candidates")
async def list_active_learning_candidates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ActiveLearningCandidate).order_by(ActiveLearningCandidate.created_at.desc())
    )
    candidates = result.scalars().all()
    
    # Map roll numbers to names for user-friendly suggestion tags
    student_results = await db.execute(select(Student))
    student_map = {s.roll_no: s.name for s in student_results.scalars().all()}
    
    res = []
    for c in candidates:
        d = c.to_dict()
        d["suggested_name"] = student_map.get(c.suggested_roll_no, "—") if c.suggested_roll_no else "—"
        res.append(d)
        
    return {
        "candidates": res,
        "total": len(res),
    }


@router.post("/active-learning/confirm")
async def confirm_active_learning_candidate(
    candidate_id: str = Form(...),
    roll_no: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    candidate = await db.get(ActiveLearningCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404, "Active Learning candidate not found.")
    
    student = await db.get(Student, roll_no)
    if not student or not student.is_active:
        raise HTTPException(404, f"Active Student {roll_no} not found.")
        
    # Move the crop photo from active_learning to student's photo directory
    student_dir = PHOTO_DIR / roll_no
    student_dir.mkdir(parents=True, exist_ok=True)
    
    src_crop_path = Path(candidate.face_crop_path)
    dest_crop_filename = f"crop_al_{uuid.uuid4().hex[:8]}.jpg"
    dest_crop_path = student_dir / dest_crop_filename
    
    if src_crop_path.exists():
        import shutil
        shutil.move(str(src_crop_path), str(dest_crop_path))
    
    # Rebuild the student's average embedding using all their face photos
    recognizer = FaceRecognizer()
    recognizer.update_student_embedding(roll_no)
    
    # Clean up files & database record
    src_emb_path = Path(candidate.embedding_path)
    if src_emb_path.exists():
        src_emb_path.unlink()
        
    await db.delete(candidate)
    await db.commit()
    
    logger.info("Confirmed AL candidate %s as %s. Student embedding updated.", candidate_id, roll_no)
    return {
        "message": "Candidate confirmed and student model updated.",
        "roll_no": roll_no,
        "candidate_id": candidate_id,
    }


@router.post("/active-learning/reject")
async def reject_active_learning_candidate(
    candidate_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    candidate = await db.get(ActiveLearningCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404, "Active Learning candidate not found.")
        
    # Delete crop and embedding files
    src_crop_path = Path(candidate.face_crop_path)
    if src_crop_path.exists():
        src_crop_path.unlink()
        
    src_emb_path = Path(candidate.embedding_path)
    if src_emb_path.exists():
        src_emb_path.unlink()
        
    await db.delete(candidate)
    await db.commit()
    
    logger.info("Rejected and deleted AL candidate %s.", candidate_id)
    return {
        "message": "Candidate rejected.",
        "candidate_id": candidate_id,
    }
