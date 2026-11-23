import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.dependencies import ensure_room_access, output, require, utcnow
from app.db.database import get_db
from app.models import (
    AgentConversation,
    AgentDecisionLog,
    AgentMessage,
    AgentReport,
    Cleanroom,
    Command,
    Device,
)
from app.services.agent_service import (
    build_operational_report,
    default_agent_provider,
    render_report_csv,
    render_report_markdown,
    serialize_decision,
    serialize_message,
    serialize_report,
)
from app.services.ecbd import accessible_rooms, as_utc
from app.services.realtime import publish_event


router = APIRouter(prefix="/agente", tags=["Agent"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: int | None = None
    cleanroom_id: int | None = None


class ConfirmIn(BaseModel):
    action: Literal["CONFIRM", "CANCEL"]


class ReportIn(BaseModel):
    cleanroom_id: int | None = None
    title: str | None = Field(default=None, max_length=180)
    report_type: str = Field(default="OPERATIONAL_SUMMARY", max_length=40)


@router.get("/decisiones")
def list_decisions(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    cleanroom_id: int | None = None,
    decision_type: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    rooms = accessible_rooms(db, user)
    room_ids = [room.id for room in rooms]
    room_codes = {room.id: room.code for room in rooms}

    query = select(AgentDecisionLog)
    count_query = select(func.count(AgentDecisionLog.id))

    if cleanroom_id is not None:
        ensure_room_access(user, cleanroom_id)
        query = query.where(AgentDecisionLog.cleanroom_id == cleanroom_id)
        count_query = count_query.where(AgentDecisionLog.cleanroom_id == cleanroom_id)
    elif "ADMINISTRADOR" not in {role.code for role in user.roles}:
        if room_ids:
            query = query.where(AgentDecisionLog.cleanroom_id.in_(room_ids))
            count_query = count_query.where(AgentDecisionLog.cleanroom_id.in_(room_ids))
        else:
            return {"items": [], "total": 0, "page": page, "size": size}

    if decision_type:
        normalized = decision_type.upper()
        query = query.where(AgentDecisionLog.decision_type == normalized)
        count_query = count_query.where(AgentDecisionLog.decision_type == normalized)

    total = db.scalar(count_query) or 0
    rows = db.scalars(
        query.order_by(desc(AgentDecisionLog.created_at), desc(AgentDecisionLog.id))
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return {
        "items": [serialize_decision(row, room_codes) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/conversaciones")
def list_conversations(
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    rows = db.scalars(
        select(AgentConversation)
        .where(AgentConversation.user_id == user.id)
        .order_by(desc(AgentConversation.updated_at), desc(AgentConversation.id))
        .limit(50)
    ).all()
    return [
        {
            "id": conv.id,
            "user_id": conv.user_id,
            "cleanroom_id": conv.cleanroom_id,
            "title": conv.title,
            "created_at": as_utc(conv.created_at).isoformat(),
            "updated_at": as_utc(conv.updated_at).isoformat(),
        }
        for conv in rows
    ]


@router.get("/conversaciones/{id}")
def get_conversation(
    id: int,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    conv = db.get(AgentConversation, id)
    if not conv:
        raise HTTPException(404, "Conversación no encontrada")
    if conv.user_id != user.id and "ADMINISTRADOR" not in {r.code for r in user.roles}:
        raise HTTPException(403, "No tienes acceso a esta conversación")
    messages = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == conv.id)
        .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
    ).all()
    return {
        "id": conv.id,
        "user_id": conv.user_id,
        "cleanroom_id": conv.cleanroom_id,
        "title": conv.title,
        "created_at": as_utc(conv.created_at).isoformat(),
        "updated_at": as_utc(conv.updated_at).isoformat(),
        "messages": [serialize_message(m) for m in messages],
    }


@router.post("/chat")
def chat_with_agent(
    data: ChatIn,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    if data.cleanroom_id is not None:
        ensure_room_access(user, data.cleanroom_id)
        if not db.get(Cleanroom, data.cleanroom_id):
            raise HTTPException(404, "Cuarto no encontrado")

    if data.conversation_id is not None:
        conv = db.get(AgentConversation, data.conversation_id)
        if not conv:
            raise HTTPException(404, "Conversación no encontrada")
        if conv.user_id != user.id and "ADMINISTRADOR" not in {r.code for r in user.roles}:
            raise HTTPException(403, "No tienes acceso a esta conversación")
    else:
        conv = AgentConversation(
            user_id=user.id,
            cleanroom_id=data.cleanroom_id,
            title=data.message.strip()[:80] or "Consulta de supervisión",
        )
        db.add(conv)
        db.flush()

    history = list(
        db.scalars(
            select(AgentMessage)
            .where(AgentMessage.conversation_id == conv.id)
            .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
        ).all()
    )

    user_msg = AgentMessage(
        conversation_id=conv.id,
        sender="USER",
        content=data.message.strip(),
        confirmation_status="NONE",
    )
    db.add(user_msg)
    db.flush()

    reply = default_agent_provider.generate_reply(
        db=db,
        user=user,
        message=data.message,
        cleanroom_id=data.cleanroom_id or conv.cleanroom_id,
        history=history,
    )

    agent_msg = AgentMessage(
        conversation_id=conv.id,
        sender="AGENT",
        content=reply.content,
        intent=reply.intent,
        references_json=json.dumps(reply.references),
        pending_command_json=json.dumps(reply.pending_command) if reply.pending_command else None,
        confirmation_status="PENDING" if reply.pending_command else "NONE",
    )
    db.add(agent_msg)
    conv.updated_at = utcnow()
    db.commit()
    db.refresh(user_msg)
    db.refresh(agent_msg)

    return {
        "conversation_id": conv.id,
        "user_message": {
            "id": user_msg.id,
            "sender": user_msg.sender,
            "content": user_msg.content,
            "created_at": as_utc(user_msg.created_at).isoformat(),
        },
        "agent_message": {
            "id": agent_msg.id,
            "sender": agent_msg.sender,
            "content": agent_msg.content,
            "intent": agent_msg.intent,
            "references": reply.references,
            "pending_command": reply.pending_command,
            "confirmation_status": agent_msg.confirmation_status,
            "created_at": as_utc(agent_msg.created_at).isoformat(),
        },
    }


@router.post("/mensajes/{id}/confirmar")
def confirm_agent_message(
    id: int,
    data: ConfirmIn,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    msg = db.get(AgentMessage, id)
    if not msg:
        raise HTTPException(404, "Mensaje no encontrado")
    conv = db.get(AgentConversation, msg.conversation_id)
    if not conv:
        raise HTTPException(404, "Conversación no encontrada")
    if conv.user_id != user.id and "ADMINISTRADOR" not in {r.code for r in user.roles}:
        raise HTTPException(403, "No tienes acceso a este mensaje")
    if msg.sender != "AGENT" or not msg.pending_command_json:
        raise HTTPException(400, "El mensaje no tiene un comando pendiente")
    if msg.confirmation_status != "PENDING":
        raise HTTPException(400, "El comando ya fue procesado")

    if data.action == "CANCEL":
        msg.confirmation_status = "CANCELLED"
        db.commit()
        return {
            "message_id": msg.id,
            "confirmation_status": "CANCELLED",
            "command_id": None,
            "decision_id": None,
        }

    pending = json.loads(msg.pending_command_json)
    device = db.get(Device, int(pending["device_id"]))
    if not device:
        raise HTTPException(404, "Dispositivo objetivo no encontrado")
    ensure_room_access(user, device.cleanroom_id)

    cmd_name = pending["command"]
    cmd_payload = pending.get("payload", {})
    cmd = Command(
        device_id=device.id,
        command=cmd_name,
        payload_json=json.dumps(cmd_payload),
    )
    decision = AgentDecisionLog(
        cleanroom_id=device.cleanroom_id,
        device_id=device.id,
        user_id=user.id,
        rule_code="AGENT-CMD",
        decision_type="COMMAND",
        severity="WARNING" if cmd_name in {"bloquear", "alarma"} else "INFO",
        summary=f"Comando '{cmd_name}' confirmado por {user.full_name} para {device.code}",
        details_json=json.dumps(
            {"message_id": msg.id, "command": cmd_name, "payload": cmd_payload}
        ),
    )
    msg.confirmation_status = "CONFIRMED"
    db.add(cmd)
    db.add(decision)
    db.commit()
    db.refresh(cmd)
    db.refresh(decision)

    publish_event("command_created", output(cmd))
    publish_event("agent_decision", output(decision))

    return {
        "message_id": msg.id,
        "confirmation_status": "CONFIRMED",
        "command_id": cmd.id,
        "decision_id": decision.id,
    }


@router.get("/reportes")
def list_reports(
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    query = select(AgentReport).order_by(desc(AgentReport.created_at), desc(AgentReport.id)).limit(50)
    if "ADMINISTRADOR" not in {r.code for r in user.roles}:
        room_ids = [r.id for r in user.assigned_cleanrooms]
        if room_ids:
            query = query.where(
                (AgentReport.cleanroom_id.in_(room_ids)) | (AgentReport.cleanroom_id.is_(None))
            )
        else:
            query = query.where(AgentReport.created_by == user.id)
    rows = db.scalars(query).all()
    return [serialize_report(r) for r in rows]


@router.post("/reportes", status_code=201)
def create_report(
    data: ReportIn | None = None,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    req = data or ReportIn()
    if req.cleanroom_id is not None and not db.get(Cleanroom, req.cleanroom_id):
        raise HTTPException(404, "Cuarto no encontrado")
    report = build_operational_report(
        db=db,
        user=user,
        cleanroom_id=req.cleanroom_id,
        title=req.title,
        report_type=req.report_type,
    )
    return serialize_report(report)


@router.get("/reportes/{id}/export")
def export_report(
    id: int,
    format: Literal["json", "md", "csv"] = Query("json"),
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    report = db.get(AgentReport, id)
    if not report:
        raise HTTPException(404, "Reporte no encontrado")
    if report.cleanroom_id is not None:
        ensure_room_access(user, report.cleanroom_id)

    if format == "json":
        content = json.dumps(serialize_report(report), indent=2, ensure_ascii=False)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="reporte-{id}.json"'},
        )
    if format == "md":
        content = render_report_markdown(report)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="reporte-{id}.md"'},
        )
    content = render_report_csv(report)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="reporte-{id}.csv"'},
    )
