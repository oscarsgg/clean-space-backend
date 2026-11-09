from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, now


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    cleanroom_id: Mapped[int] = mapped_column(ForeignKey("cleanrooms.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(40))
    serial_number: Mapped[str] = mapped_column(String(100), unique=True)
    api_key_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rssi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    free_memory: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)