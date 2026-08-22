"""Student profile editing while preserving recognition and attendance data."""

import logging
import os
import uuid
from pathlib import Path
from typing import List

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.active_learning import ActiveLearningCandidate
from backend.models.attendance import AttendanceRecord
from backend.models.student import Student
from backend.services.face_detector import FaceDetector
from backend.services.recognizer import (
    EMBED_DIR,
    IMAGE_SUFFIXES,
    FaceRecognizer,
    get_shared_app,
    invalidate_gallery,
)
from backend.services.student_validation import (
    MAX_PHOTO_BYTES,
    MAX_PHOTOS_PER_REQUEST,
    is_valid_roll_no,
    normalize_student_info,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/students", tags=["students"])

PHOTO_DIR = Path(os.getenv("PHOTO_DIR") or os.getenv("STUDENT_PHOTOS_DIR") or "data/student_photos")


def _photo_payload(roll_no: str) -> list[dict]:
    student_dir = PHOTO_DIR / roll_no
    if not student_dir.exists():
        return []
    photos = [
        path for path in student_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    photos.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [
        {
            "filename": path.name,
            "url": f"/api/students/{roll_no}/photos/{path.name}",
        }
        for path in photos
    ]


async def _get_active_student(roll_no: str, db: AsyncSession) -> Student:
    if not is_valid_roll_no(roll_no):
        raise HTTPException(400, "Invalid roll number.")
    student = await db.get(Student, roll_no)
    if not student or not student.is_active:
        raise HTTPException(404, f"Student {roll_no} not found.")
    return student


@router.get("/{roll_no}/photos")
async def list_student_photos(
    roll_no: str,
    db: AsyncSession = Depends(get_db),
):
    """List the enrollment photos stored for a student."""
    student = await _get_active_student(roll_no, db)
    photos = _photo_payload(roll_no)
    return {
        "roll_no": student.roll_no,
        "name": student.name,
        "photos": photos,
        "total": len(photos),
    }


@router.get("/{roll_no}/photos/{filename}")
async def get_student_photo(
    roll_no: str,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Serve one enrollment photo after validating its student and filename."""
    await _get_active_student(roll_no, db)
    if Path(filename).name != filename or Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(400, "Invalid photo filename.")
    photo_path = PHOTO_DIR / roll_no / filename
    if not photo_path.is_file():
        raise HTTPException(404, "Photo not found.")
    return FileResponse(photo_path)


@router.delete("/{roll_no}/photos/{filename}")
async def delete_student_photo(
    roll_no: str,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete one photo and rebuild recognition data from those remaining."""
    student = await _get_active_student(roll_no, db)
    if Path(filename).name != filename or Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(400, "Invalid photo filename.")
    photo_path = PHOTO_DIR / roll_no / filename
    if not photo_path.is_file():
        raise HTTPException(404, "Photo not found.")

    staged_path = photo_path.with_name(f".{photo_path.name}.{uuid.uuid4().hex}.pending-delete")
    photo_path.rename(staged_path)
    recognizer = FaceRecognizer()
    try:
        recognizer.update_student_embedding(roll_no)
        staged_path.unlink()
    except Exception as exc:
        if staged_path.exists():
            staged_path.rename(photo_path)
        try:
            recognizer.update_student_embedding(roll_no)
        except Exception:
            logger.exception("Could not restore embeddings after failed photo deletion for %s", roll_no)
        logger.exception("Could not rebuild embeddings after deleting a photo for %s", roll_no)
        raise HTTPException(500, "Could not update recognition data; the photo was restored.") from exc

    remaining_photos = _photo_payload(roll_no)
    quality = recognizer.enrollment_quality(roll_no)
    logger.info("Deleted enrollment photo %s for %s", filename, roll_no)
    return {
        "roll_no": student.roll_no,
        "name": student.name,
        "deleted": filename,
        "photos": remaining_photos,
        "total": len(remaining_photos),
        "enrollment_quality": quality["quality"],
        "recognition_available": quality["embeddings"] > 0,
    }


@router.post("/{roll_no}/photos")
async def add_student_photos(
    roll_no: str,
    photos: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Add enrollment photos and their face embeddings to an existing student."""
    student = await _get_active_student(roll_no, db)
    if not photos:
        raise HTTPException(400, "Select at least one photo.")
    if len(photos) > MAX_PHOTOS_PER_REQUEST:
        raise HTTPException(400, f"Upload a maximum of {MAX_PHOTOS_PER_REQUEST} photos at a time.")

    detector = FaceDetector(app=get_shared_app())
    recognizer = FaceRecognizer()
    student_dir = PHOTO_DIR / roll_no
    student_dir.mkdir(parents=True, exist_ok=True)
    embeddings = []
    saved_paths: list[Path] = []
    rejected = 0

    for upload in photos:
        data = await upload.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            rejected += 1
            continue
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            rejected += 1
            continue
        faces = detector.detect(image, is_group=False)
        if not faces:
            rejected += 1
            continue
        face = max(
            faces,
            key=lambda item: (item["bbox"][2] - item["bbox"][0])
            * (item["bbox"][3] - item["bbox"][1]),
        )
        embedding = face.get("embedding")
        if embedding is None:
            embedding = recognizer.get_embedding(face["face_crop"])
        if embedding is None:
            rejected += 1
            continue

        photo_path = student_dir / f"{uuid.uuid4().hex[:8]}.jpg"
        if not cv2.imwrite(str(photo_path), image):
            rejected += 1
            continue
        saved_paths.append(photo_path)
        embeddings.append(embedding)

    if not embeddings:
        if student_dir.exists() and not any(student_dir.iterdir()):
            student_dir.rmdir()
        raise HTTPException(400, "No valid face was detected in the selected photos.")

    previous_embeddings = recognizer.load_embeddings(roll_no)
    try:
        recognizer.add_embeddings(roll_no, embeddings)
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        if previous_embeddings is not None:
            recognizer.save_embeddings(roll_no, list(previous_embeddings))
        else:
            recognizer.remove_embedding(roll_no)
        raise

    all_photos = _photo_payload(roll_no)
    logger.info("Added %d enrollment photo(s) for %s", len(embeddings), roll_no)
    return {
        "roll_no": student.roll_no,
        "name": student.name,
        "photos_added": len(embeddings),
        "photos_rejected": rejected,
        "photos": all_photos,
        "total": len(all_photos),
        "enrollment_quality": recognizer.enrollment_quality(roll_no)["quality"],
    }


@router.put("/{roll_no}")
async def update_student(
    roll_no: str,
    new_roll_no: str = Form(...),
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Edit a student's name and/or roll number without losing related data."""
    try:
        new_roll_no, name = normalize_student_info(new_roll_no, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    student = await db.get(Student, roll_no)
    if not student or not student.is_active:
        raise HTTPException(404, f"Student {roll_no} not found.")

    candidate_result = await db.execute(
        select(ActiveLearningCandidate).where(
            ActiveLearningCandidate.suggested_roll_no == roll_no
        )
    )
    candidates = candidate_result.scalars().all()

    if new_roll_no == roll_no:
        student.name = name
        for candidate in candidates:
            candidate.suggested_name = name
        await db.commit()
        return {"old_roll_no": roll_no, "roll_no": roll_no, "name": name}

    if await db.get(Student, new_roll_no):
        raise HTTPException(409, f"Roll number {new_roll_no} is already in use.")

    target_attendance_result = await db.execute(
        select(AttendanceRecord.id).where(AttendanceRecord.roll_no == new_roll_no).limit(1)
    )
    if target_attendance_result.scalar_one_or_none() is not None:
        raise HTTPException(409, f"Attendance data already exists for {new_roll_no}.")

    attendance_result = await db.execute(
        select(AttendanceRecord).where(AttendanceRecord.roll_no == roll_no)
    )
    attendance_records = attendance_result.scalars().all()
    path_pairs = [
        (PHOTO_DIR / roll_no, PHOTO_DIR / new_roll_no),
        (EMBED_DIR / f"{roll_no}.npz", EMBED_DIR / f"{new_roll_no}.npz"),
        (EMBED_DIR / f"{roll_no}.npy", EMBED_DIR / f"{new_roll_no}.npy"),
    ]
    if any(destination.exists() for _, destination in path_pairs):
        raise HTTPException(409, f"Recognition data already exists for {new_roll_no}.")

    moved_paths: list[tuple[Path, Path]] = []
    try:
        for source, destination in path_pairs:
            if source.exists():
                source.rename(destination)
                moved_paths.append((source, destination))

        student.roll_no = new_roll_no
        student.name = name
        for record in attendance_records:
            record.roll_no = new_roll_no
            record.id = f"{new_roll_no}_{record.date}"
        for candidate in candidates:
            candidate.suggested_roll_no = new_roll_no
            candidate.suggested_name = name
        await db.commit()
    except Exception:
        await db.rollback()
        for source, destination in reversed(moved_paths):
            if destination.exists() and not source.exists():
                destination.rename(source)
        raise

    invalidate_gallery()
    logger.info("Updated student %s to %s (%s)", roll_no, new_roll_no, name)
    return {"old_roll_no": roll_no, "roll_no": new_roll_no, "name": name}
