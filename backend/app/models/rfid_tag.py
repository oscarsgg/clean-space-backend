from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class RfidTag(Base):
    __tablename__ = "rfid_tags"

    uid: Mapped[str] = mapped_column(String(64), primary_key=True)
    isActive: Mapped[bool] = mapped_column(Boolean, default=True)
    assigned_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    employee = relationship("Employee", back_populates="rfid_tags")