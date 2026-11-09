from datetime import date

from sqlalchemy import Boolean, Date, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    firstName: Mapped[str] = mapped_column(String(100), index=True)
    lastName: Mapped[str] = mapped_column(String(100), index=True)
    department: Mapped[str] = mapped_column(String(100), index=True)
    isActive: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    rfid_tags = relationship("RfidTag", back_populates="employee")