"""
routers/attendance.py
Attendance processing and record endpoints.
"""

import asyncio
import logging
import os
import uuid
from datetime import date, datetime
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.attendance import AttendanceRecord
from backend.models.student import Student
from backend.services.excel_export import build_attendance_excel
from backend.services.video_processor import UPLOAD_DIR, VideoProcessor
from backend.models.evaluation import EvaluationRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attendance", tags=["attendance"])

_video_processor: Optional[VideoProcessor] = None
_video_processing_lock = asyncio.Lock()
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".wmv", ".m4v"}
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_UPLOAD_MB", "250")) * 1024 * 1024

def get_video_processor() -> VideoProcessor:
    global _video_processor
    if _video_processor is None:
        _video_processor = VideoProcessor()
    return _video_processor


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid date: {value}") from exc


async def _read_video_upload(video: UploadFile) -> tuple[bytes, str]:
    extension = Path(video.filename or "").suffix.lower() or ".mp4"
    if extension not in VIDEO_EXTENSIONS:
        raise HTTPException(400, f"Unsupported video type: {extension}")
    video_bytes = await video.read(MAX_VIDEO_BYTES + 1)
    if not video_bytes:
        raise HTTPException(400, "Video is required.")
    if len(video_bytes) > MAX_VIDEO_BYTES:
        raise HTTPException(413, f"Video must be {MAX_VIDEO_BYTES // (1024 * 1024)} MB or smaller.")
    return video_bytes, extension


async def _delete_unreferenced_class_photos(paths: set[str], db: AsyncSession) -> None:
    upload_root = UPLOAD_DIR.resolve()
    for path_value in paths:
        still_used = await db.execute(
            select(AttendanceRecord.id)
            .where(AttendanceRecord.class_photo_path == path_value)
            .limit(1)
        )
        if still_used.scalar_one_or_none() is not None:
            continue
        photo_path = Path(path_value)
        try:
            if photo_path.parent.resolve() == upload_root:
                photo_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove unused class photo %s", photo_path)


async def _records_with_names(class_date: date, db: AsyncSession, active_only: bool = True) -> list:
    """
    Fetch attendance records for a given date, joined with student names.
    If active_only=True, only returns records for currently active students.
    Hard-deleted students have no records (they are cascade-deleted).
    Soft-deleted students (is_active=False) are filtered out when active_only=True.
    """
    query = (
        select(AttendanceRecord, Student.name, Student.is_active)
        .join(Student, Student.roll_no == AttendanceRecord.roll_no, isouter=True)
        .where(AttendanceRecord.date == class_date)
        .order_by(AttendanceRecord.roll_no)
    )
    result = await db.execute(query)
    records = []
    for record, name, is_active in result.all():
        # Skip records for deleted or inactive students
        if active_only and (name is None or is_active is False):
            continue
        row = record.to_dict()
        row["name"] = name or "—"
        records.append(row)
    return records

@router.post("/process-video")
async def process_video_attendance(
    video: UploadFile = File(...),
    date_str: Optional[str] = Form(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    video_bytes, extension = await _read_video_upload(video)

    class_date = _parse_date(date_str) if date_str else date.today()
    processor = get_video_processor()

    async def event_generator():
        async with _video_processing_lock:
            try:
                async for event in processor.process_video(video_bytes, class_date, db, filename_ext=extension):
                    yield json.dumps(event) + "\n"
            except Exception:
                await db.rollback()
                logger.exception("Video attendance processing failed")
                yield json.dumps({"type": "error", "message": "Video processing failed. Please try again."}) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@router.get("/export/excel")
async def export_excel(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    start = _parse_date(start_date) if start_date else None
    end = _parse_date(end_date) if end_date else None
    if start and end and start > end:
        raise HTTPException(400, "Start date must be on or before end date.")
    content = await build_attendance_excel(db, start, end)

    filename = f"attendance_report_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/student/{roll_no}")
async def student_attendance(roll_no: str, db: AsyncSession = Depends(get_db)):
    student = await db.get(Student, roll_no)
    if not student:
        raise HTTPException(404, f"Student {roll_no} not found.")

    result = await db.execute(
        select(AttendanceRecord)
        .where(AttendanceRecord.roll_no == roll_no)
        .order_by(AttendanceRecord.date)
    )
    records = [r.to_dict() for r in result.scalars().all()]

    for row in records:
        row["name"] = student.name

    present = sum(1 for r in records if r["status"] == "P")
    total_days = len(records)
    percentage = round(present / total_days * 100, 1) if total_days else 0.0

    return {
        "roll_no": roll_no,
        "name": student.name,
        "present": present,
        "total_days": total_days,
        "percentage": percentage,
        "records": records,
    }


@router.put("/{class_date}/{roll_no}")
async def override_attendance(
    class_date: str,
    roll_no: str,
    status: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    status = status.strip().upper()
    if status not in ("P", "A"):
        raise HTTPException(400, "Status must be P or A.")

    parsed_date = _parse_date(class_date)
    result = await db.execute(
        select(AttendanceRecord).where(
            AttendanceRecord.roll_no == roll_no,
            AttendanceRecord.date == parsed_date,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, f"No attendance record for {roll_no} on {class_date}.")

    record.status = status
    if status == "A":
        record.confidence = 0.0
    await db.commit()

    return {"roll_no": roll_no, "date": str(parsed_date), "status": status}


@router.delete("/cleanup/orphaned")
async def cleanup_orphaned_records(db: AsyncSession = Depends(get_db)):
    """
    Delete attendance records that belong to students who no longer exist.
    Inactive students are intentionally retained because soft deletion promises
    to preserve their attendance history.
    """
    # Find records whose student row no longer exists.
    result = await db.execute(
        select(AttendanceRecord, Student.is_active)
        .join(Student, Student.roll_no == AttendanceRecord.roll_no, isouter=True)
    )
    orphaned = []
    for record, is_active in result.all():
        if is_active is None:
            orphaned.append(record)

    photo_paths = {rec.class_photo_path for rec in orphaned if rec.class_photo_path}
    for rec in orphaned:
        await db.delete(rec)

    if orphaned:
        await db.commit()
        await _delete_unreferenced_class_photos(photo_paths, db)

    logger.info("Cleaned up %d orphaned attendance records.", len(orphaned))
    return {
        "orphaned_deleted": len(orphaned),
        "message": f"Cleaned up {len(orphaned)} orphaned attendance records.",
    }



@router.post("/evaluate")
async def evaluate_attendance(
    video: UploadFile = File(...),
    ground_truth_rolls: str = Form(...),  # comma separated
    date_str: Optional[str] = Form(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    """
    Run an evaluation of the system's accuracy.
    The teacher uploads a video and provides the comma-separated roll numbers of who is ACTUALLY present.
    The system processes the video (without saving to main attendance records), compares with ground truth,
    and returns Precision/Recall/F1 metrics.
    """
    video_bytes, extension = await _read_video_upload(video)

    class_date = _parse_date(date_str) if date_str else date.today()
    processor = get_video_processor()

    gt_list = [r.strip() for r in ground_truth_rolls.split(",") if r.strip()]
    gt_set = set(gt_list)
    if not gt_set:
        raise HTTPException(400, "Enter at least one ground-truth roll number.")

    active_result = await db.execute(select(Student.roll_no).where(Student.is_active.is_(True)))
    active_rolls = set(active_result.scalars().all())
    unknown_rolls = sorted(gt_set - active_rolls)
    if unknown_rolls:
        raise HTTPException(400, f"Unknown or inactive roll number(s): {', '.join(unknown_rolls)}")
    
    try:
        result = None
        async with _video_processing_lock:
            async for event in processor.process_video(
                video_bytes,
                class_date,
                db,
                filename_ext=extension,
                persist_records=False,
                create_active_learning=False,
            ):
                if event["type"] == "result":
                    result = event["data"]
                elif event["type"] == "error":
                    raise ValueError(event["message"])
        if result is None:
            raise ValueError("No result returned from video processor")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
        
    predicted_present_set = set([d["roll_no"] for d in result["details"] if d["status"] == "P"])
    
    # Calculate metrics
    tp = len(gt_set.intersection(predicted_present_set))
    fp = len(predicted_present_set - gt_set)
    fn = len(gt_set - predicted_present_set)
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    metrics = {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1_score": f1_score
    }
    
    # Save evaluation record
    record = EvaluationRecord(
        id=uuid.uuid4().hex,
        eval_date=class_date,
        total_students=result["total_students"],
        ground_truth_present=len(gt_set),
        predicted_present=len(predicted_present_set),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        created_at=datetime.utcnow()
    )
    db.add(record)
    await db.commit()
    
    return {"status": "success", "metrics": metrics, "details": result["details"]}


@router.get("/metrics/summary")
async def get_metrics_summary(db: AsyncSession = Depends(get_db)):
    """Return aggregated evaluation metrics."""
    result = await db.execute(select(EvaluationRecord))
    records = result.scalars().all()
    
    if not records:
        return {"total_sessions": 0}
        
    avg_precision = sum(r.precision for r in records) / len(records)
    avg_recall = sum(r.recall for r in records) / len(records)
    avg_f1 = sum(r.f1_score for r in records) / len(records)
    
    return {
        "total_sessions": len(records),
        "avg_precision": avg_precision,
        "avg_recall": avg_recall,
        "avg_f1": avg_f1
    }


@router.get("/metrics/history")
async def get_metrics_history(db: AsyncSession = Depends(get_db)):
    """Return history of evaluation sessions."""
    result = await db.execute(select(EvaluationRecord).order_by(EvaluationRecord.created_at.desc()))
    records = result.scalars().all()
    return {"sessions": [r.to_dict() for r in records]}


@router.delete("/metrics/history")
async def clear_metrics_history(db: AsyncSession = Depends(get_db)):
    """Delete all evaluation history records."""
    result = await db.execute(select(EvaluationRecord))
    records = result.scalars().all()
    count = len(records)
    for rec in records:
        await db.delete(rec)
    await db.commit()
    logger.info("Cleared %d evaluation history records.", count)
    return {"deleted": count, "message": f"Cleared {count} evaluation session(s)."}


@router.delete("/{class_date}")
async def reset_attendance_for_date(
    class_date: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete all attendance records and unused class photos for one date."""
    parsed_date = _parse_date(class_date)
    att_result = await db.execute(
        select(AttendanceRecord).where(AttendanceRecord.date == parsed_date)
    )
    att_records = att_result.scalars().all()
    records_deleted = len(att_records)
    photo_paths = {record.class_photo_path for record in att_records if record.class_photo_path}
    for record in att_records:
        await db.delete(record)

    await db.commit()
    await _delete_unreferenced_class_photos(photo_paths, db)
    logger.info("Reset attendance for %s — %d records deleted.", parsed_date, records_deleted)
    return {
        "date": str(parsed_date),
        "records_deleted": records_deleted,
        "message": f"Attendance for {parsed_date} has been reset.",
    }


@router.get("/{class_date}")
async def attendance_by_date(class_date: str, db: AsyncSession = Depends(get_db)):
    parsed_date = _parse_date(class_date)
    records = await _records_with_names(parsed_date, db)

    present = sum(1 for r in records if r["status"] == "P")
    absent = sum(1 for r in records if r["status"] == "A")

    return {
        "date": str(parsed_date),
        "records": records,
        "present": present,
        "absent": absent,
        "total": len(records),
    }
