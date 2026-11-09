from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, String, Text, UniqueConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, now


class SensorReading(Base):
    __tablename__ = "sensor_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    cleanroom_id: Mapped[int] = mapped_column(ForeignKey("cleanrooms.id"), index=True)
    metric: Mapped[str] = mapped_column(String(50), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MonitoringParameter(Base):
    __tablename__ = "monitoring_parameters"

    id: Mapped[int] = mapped_column(primary_key=True)
    cleanroom_id: Mapped[int] = mapped_column(ForeignKey("cleanrooms.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(50))
    min_value: Mapped[float] = mapped_column(Float)
    max_value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("cleanroom_id", "metric"),)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    cleanroom_id: Mapped[int] = mapped_column(ForeignKey("cleanrooms.id"), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    reading_id: Mapped[int | None] = mapped_column(ForeignKey("sensor_readings.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)