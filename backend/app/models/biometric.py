from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, now


class EmployeeBiometric(Base):
    __tablename__ = "employee_biometrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    sample_type: Mapped[str] = mapped_column(String(50), default="full_face")  # "full_face", "masked_cleanroom", "eyes_only"
    full_face_vector: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded 128-float array
    periocular_vector: Mapped[str] = mapped_column(Text)  # JSON-encoded 128-float array for eyes/periocular region
    notes: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    employee = relationship("Employee", backref="biometrics")
