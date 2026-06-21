"""
routers/attendance.py
Attendance processing and record endpoints.
"""

import logging
import uuid
from datetime import date, datetime
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.attendance import AttendanceRecord
from backend.models.student import Student
from backend.services.excel_export import build_attendance_excel
from backend.services.video_processor import VideoProcessor
from backend.models.evaluation import EvaluationRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attendance", tags=["attendance"])

_video_processor: Optional[VideoProcessor] = None

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
    video_bytes = await video.read()
    if not video_bytes:
        raise HTTPException(400, "Video is required.")

    class_date = _parse_date(date_str) if date_str else date.today()
    processor = get_video_processor()

    import os
    ext = ".mp4"
    if video.filename:
        _, ext_val = os.path.splitext(video.filename)
        if ext_val:
            ext = ext_val
    
    try:
        async def event_generator():
            try:
                async for event in processor.process_video(video_bytes, class_date, db, filename_ext=ext):
                    yield json.dumps(event) + "\n"
            except Exception as exc:
                yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
                
        return StreamingResponse(event_generator(), media_type="application/x-ndjson")
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/export/excel")
async def export_excel(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    start = _parse_date(start_date) if start_date else None
    end = _parse_date(end_date) if end_date else None
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
    if status not in ("P", "A"):
        raise HTTPException(400, "Status must be P or A.")

    parsed_date = _parse_date(class_date)
    record_id = f"{roll_no}_{parsed_date}"
    record = await db.get(AttendanceRecord, record_id)
    if not record:
        raise HTTPException(404, f"No attendance record for {roll_no} on {class_date}.")

    record.status = status
    if status == "A":
        record.confidence = 0.0
    await db.commit()

    return {"roll_no": roll_no, "date": str(parsed_date), "status": status}


@router.delete("/{class_date}")
async def reset_attendance_for_date(
    class_date: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete ALL attendance records for a given date.
    Used when the user wants to clear and re-process from scratch.
    """
    parsed_date = _parse_date(class_date)

    # Count and delete attendance records
    att_result = await db.execute(
        select(AttendanceRecord).where(AttendanceRecord.date == parsed_date)
    )
    att_records = att_result.scalars().all()
    records_deleted = len(att_records)
    for rec in att_records:
        await db.delete(rec)

    await db.commit()

    logger.info(
        "Reset attendance for %s — %d records deleted.",
        parsed_date, records_deleted,
    )
    return {
        "date": str(parsed_date),
        "records_deleted": records_deleted,
        "message": f"Attendance for {parsed_date} has been reset.",
    }


@router.delete("/cleanup/orphaned")
async def cleanup_orphaned_records(db: AsyncSession = Depends(get_db)):
    """
    Delete attendance records that belong to students who no longer exist
    or are inactive (soft-deleted). Useful for cleaning stale data.
    """
    # Find records where the student is missing or inactive
    result = await db.execute(
        select(AttendanceRecord, Student.is_active)
        .join(Student, Student.roll_no == AttendanceRecord.roll_no, isouter=True)
    )
    orphaned = []
    for record, is_active in result.all():
        if is_active is None or is_active is False:
            orphaned.append(record)

    for rec in orphaned:
        await db.delete(rec)

    if orphaned:
        await db.commit()

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
    import os
    video_bytes = await video.read()
    if not video_bytes:
        raise HTTPException(400, "Video is required.")

    class_date = _parse_date(date_str) if date_str else date.today()
    processor = get_video_processor()

    ext = ".mp4"
    if video.filename:
        _, ext_val = os.path.splitext(video.filename)
        if ext_val:
            ext = ext_val
    
    gt_list = [r.strip() for r in ground_truth_rolls.split(",") if r.strip()]
    gt_set = set(gt_list)
    
    try:
        result = None
        async for event in processor.process_video(video_bytes, class_date, db, filename_ext=ext):
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
