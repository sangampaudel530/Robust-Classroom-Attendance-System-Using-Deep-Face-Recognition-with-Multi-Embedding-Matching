"""
models/attendance.py
Attendance record ORM model.
"""

from datetime import date

from sqlalchemy import Date, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    roll_no: Mapped[str] = mapped_column(String(32), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(1))  # P or A
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    class_photo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    def to_dict(self) -> dict:
        return {
            "roll_no": self.roll_no,
            "date": str(self.date),
            "status": self.status,
            "confidence": self.confidence,
            "class_photo_path": self.class_photo_path,
        }
