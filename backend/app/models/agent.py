from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, now


class AgentDecisionLog(Base):
    __tablename__ = "agent_decision_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    cleanroom_id: Mapped[int | None] = mapped_column(
        ForeignKey("cleanrooms.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rule_code: Mapped[str] = mapped_column(String(30), index=True)
    decision_type: Mapped[str] = mapped_column(String(30), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="INFO")
    summary: Mapped[str] = mapped_column(String(255))
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cleanroom_id: Mapped[int | None] = mapped_column(
        ForeignKey("cleanrooms.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(150), default="Consulta de supervisión")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True
    )
    sender: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(60), nullable=True)
    references_json: Mapped[str] = mapped_column(Text, default="[]")
    pending_command_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_status: Mapped[str] = mapped_column(String(20), default="NONE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AgentReport(Base):
    __tablename__ = "agent_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cleanroom_id: Mapped[int | None] = mapped_column(
        ForeignKey("cleanrooms.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(180))
    report_type: Mapped[str] = mapped_column(String(40), default="OPERATIONAL_SUMMARY")
    summary_text: Mapped[str] = mapped_column(Text)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
