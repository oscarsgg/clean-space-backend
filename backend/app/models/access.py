from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, now


class AccessPermission(Base):
    __tablename__ = "access_permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cleanroom_id: Mapped[int] = mapped_column(ForeignKey("cleanrooms.id", ondelete="CASCADE"), index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("user_id", "cleanroom_id"),)


class AccessEvent(Base):
    __tablename__ = "access_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    cleanroom_id: Mapped[int] = mapped_column(ForeignKey("cleanrooms.id"), index=True)
    rfid_card_id: Mapped[int | None] = mapped_column(ForeignKey("rfid_cards.id"), nullable=True)
    source_device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(30), default="RFID")
    direction: Mapped[str] = mapped_column(String(20), default="ENTRY")
    result: Mapped[str] = mapped_column(String(20))
    denial_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class DoorPermission(Base):
    __tablename__ = "door_access_permissions"
    __table_args__ = (UniqueConstraint("rfid_uid", "door_id", name="uq_door_permission_rfid_door"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rfid_uid: Mapped[str] = mapped_column(ForeignKey("rfid_tags.uid", ondelete="CASCADE"), index=True)
    door_id: Mapped[str] = mapped_column(String(100), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[date] = mapped_column(Date, default=date.today, index=True)


class AccessLog(Base):
    __tablename__ = "access_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    rfid_uid: Mapped[str] = mapped_column(String(64), index=True)
    render_id: Mapped[str] = mapped_column(String(100), index=True)
    access_status: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[date] = mapped_column(Date, default=date.today, index=True)