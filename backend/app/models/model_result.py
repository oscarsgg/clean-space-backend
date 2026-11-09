from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, now


class ModelResult(Base):
    __tablename__ = "model_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    cleanroom_id: Mapped[int | None] = mapped_column(ForeignKey("cleanrooms.id"), nullable=True)
    model_type: Mapped[str] = mapped_column(String(40))
    metric: Mapped[str | None] = mapped_column(String(50), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)