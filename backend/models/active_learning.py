from datetime import date as date_type, datetime

from sqlalchemy import Date, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ActiveLearningCandidate(Base):
    __tablename__ = "active_learning_candidates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    class_date: Mapped[date_type] = mapped_column(Date)
    face_crop_path: Mapped[str] = mapped_column(String)
    suggested_roll_no: Mapped[str] = mapped_column(String, nullable=True)
    suggested_name: Mapped[str] = mapped_column(String, nullable=True)
    suggested_confidence: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "class_date": str(self.class_date),
            "face_crop_path": self.face_crop_path,
            "suggested_roll_no": self.suggested_roll_no,
            "suggested_name": self.suggested_name,
            "suggested_confidence": self.suggested_confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
