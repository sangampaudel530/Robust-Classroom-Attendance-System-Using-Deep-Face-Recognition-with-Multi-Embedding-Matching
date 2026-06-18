"""
models/evaluation.py  [NEW]

ORM model for storing per-session evaluation metrics.
Created by POST /api/attendance/evaluate when teacher provides ground truth.
"""

from datetime import date as date_type, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class EvaluationRecord(Base):
    __tablename__ = "evaluation_records"

    id:                   Mapped[str]   = mapped_column(String(64), primary_key=True)
    eval_date:            Mapped[date_type] = mapped_column(Date, index=True)
    created_at:           Mapped[datetime]  = mapped_column(DateTime, default=datetime.utcnow)
    total_students:       Mapped[int]   = mapped_column(Integer, default=0)
    ground_truth_present: Mapped[int]   = mapped_column(Integer, default=0)
    predicted_present:    Mapped[int]   = mapped_column(Integer, default=0)
    true_positives:       Mapped[int]   = mapped_column(Integer, default=0)
    false_positives:      Mapped[int]   = mapped_column(Integer, default=0)
    false_negatives:      Mapped[int]   = mapped_column(Integer, default=0)
    precision:            Mapped[float] = mapped_column(Float, default=0.0)
    recall:               Mapped[float] = mapped_column(Float, default=0.0)
    f1_score:             Mapped[float] = mapped_column(Float, default=0.0)

    def to_dict(self) -> dict:
        return {
            "id":                   self.id,
            "eval_date":            str(self.eval_date),
            "created_at":           self.created_at.isoformat() if self.created_at else None,
            "total_students":       self.total_students,
            "ground_truth_present": self.ground_truth_present,
            "predicted_present":    self.predicted_present,
            "true_positives":       self.true_positives,
            "false_positives":      self.false_positives,
            "false_negatives":      self.false_negatives,
            "precision":            self.precision,
            "recall":               self.recall,
            "f1_score":             self.f1_score,
        }