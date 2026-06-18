"""
models/student.py
Student ORM model.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Student(Base):
    __tablename__ = "students"

    roll_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def to_dict(self) -> dict:
        return {
            "roll_no": self.roll_no,
            "name": self.name,
            "enrolled_at": self.enrolled_at.isoformat() if self.enrolled_at else None,
            "is_active": self.is_active,
        }
