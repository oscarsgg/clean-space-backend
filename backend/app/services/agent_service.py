import csv
import io
import json
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.dependencies import ensure_room_access, utcnow
from app.models import (
    AccessEvent,
    AgentDecisionLog,
    AgentMessage,
    AgentReport,
    Alert,
    Cleanroom,
    Device,
    User,
)
from app.services.ecbd import accessible_rooms, as_utc, get_supervisor_summary


def _normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower().strip()


@dataclass
class AgentReply:
    content: str
    intent: str
    references: list[dict[str, Any]] = field(default_factory=list)
    pending_command: dict[str, Any] | None = None


class AgentProvider(ABC):
    """Decoupled interface for conversational supervisor agents (deterministic or LLM)."""

    @abstractmethod
    def generate_reply(
        self,
        db: Session,
        user: User,
        message: str,
        cleanroom_id: int | None = None,
        history: list[AgentMessage] | None = None,
    ) -> AgentReply:
        raise NotImplementedError


class DeterministicAgentProvider(AgentProvider):
    """Parameterized SQLAlchemy-backed operational assistant ready to swap with an LLM provider."""

    def _resolve_target_rooms(
        self, db: Session, user: User, norm_msg: str, cleanroom_id: int | None
    ) -> tuple[list[Cleanroom], Cleanroom | None]:
        rooms = accessible_rooms(db, user)
        mentioned: Cleanroom | None = None
        for room in rooms:
            code_norm = _normalize_text(room.code)
            name_norm = _normalize_text(room.name)
            if code_norm in norm_msg or (name_norm and name_norm in norm_msg):
                mentioned = room
                break
        if mentioned:
            return [mentioned], mentioned
        if cleanroom_id is not None:
            ensure_room_access(user, cleanroom_id)
            target = next((r for r in rooms if r.id == cleanroom_id), None)
            if target:
                return [target], target
        return rooms, rooms[0] if rooms else None

    def _find_room_device(self, db: Session, cleanroom_id: int) -> Device | None:
        device = db.scalar(
            select(Device)
            .where(Device.cleanroom_id == cleanroom_id, Device.status == "ACTIVE")
            .order_by(Device.id.asc())
        )
        if not device:
            device = db.scalar(
                select(Device).where(Device.cleanroom_id == cleanroom_id).order_by(Device.id.asc())
            )
        return device

    def generate_reply(
        self,
        db: Session,
        user: User,
        message: str,
        cleanroom_id: int | None = None,
        history: list[AgentMessage] | None = None,
    ) -> AgentReply:
        norm = _normalize_text(message)
        scoped_rooms, primary_room = self._resolve_target_rooms(db, user, norm, cleanroom_id)
        room_ids = [r.id for r in scoped_rooms]
        refs = [{"type": "cleanroom", "id": r.id, "label": r.code} for r in scoped_rooms]

        # 1. Command proposals requiring human confirmation
        command_spec: tuple[str, str, dict[str, Any], str] | None = None
        if "desbloquear" in norm or "normal" in norm or "restablecer" in norm or "liberar" in norm:
            command_spec = (
                "UNLOCK_DEVICE",
                "normal",
                {"source": "agent_chat"},
                "restablecer el estado normal",
            )
        elif "bloquear" in norm or "bloquea" in norm or "cerrar puerta" in norm or "cerrar sala" in norm or "trabar" in norm:
            command_spec = (
                "LOCK_DEVICE",
                "bloquear",
                {"duration_seconds": 300, "source": "agent_chat"},
                "bloquear el acceso durante 300 segundos",
            )
        elif "activar alarma" in norm or "alarma" in norm or "sirena" in norm:
            command_spec = (
                "ALARM_COMMAND",
                "alarma",
                {"source": "agent_chat"},
                "activar la alarma preventiva",
            )
        elif "leer ahora" in norm or "leer_ahora" in norm or "lectura inmediata" in norm or "muestreo" in norm:
            command_spec = (
                "READ_NOW",
                "leer_ahora",
                {"source": "agent_chat"},
                "solicitar una lectura inmediata de sensores",
            )

        if command_spec is not None:
            intent, cmd_name, cmd_payload, action_desc = command_spec
            if not primary_room:
                return AgentReply(
                    content="No tienes salas limpias asignadas para ejecutar comandos.",
                    intent=intent,
                    references=[],
                    pending_command=None,
                )
            device = self._find_room_device(db, primary_room.id)
            if not device:
                return AgentReply(
                    content=f"La sala {primary_room.code} no tiene un dispositivo vinculado para enviar el comando '{cmd_name}'.",
                    intent=intent,
                    references=[{"type": "cleanroom", "id": primary_room.id, "label": primary_room.code}],
                    pending_command=None,
                )
            return AgentReply(
                content=(
                    f"Propuesta preparada para {primary_room.code} (dispositivo {device.code}): "
                    f"{action_desc}. Confirma la acción para encolar el comando '{cmd_name}'."
                ),
                intent=intent,
                references=[
                    {"type": "cleanroom", "id": primary_room.id, "label": primary_room.code},
                    {"type": "device", "id": device.id, "label": device.code},
                ],
                pending_command={
                    "device_id": device.id,
                    "command": cmd_name,
                    "payload": cmd_payload,
                },
            )

        summary = get_supervisor_summary(db, user)
        target_cards = [
            card for card in summary["rooms"] if not room_ids or card["id"] in room_ids
        ]

        # 2. Occupancy status
        if any(
            kw in norm
            for kw in ("ocupacion", "aforo", "personas", "quien esta", "dentro", "ocupantes", "capacidad")
        ):
            if not target_cards:
                return AgentReply(
                    content="No hay salas limpias disponibles para consultar ocupación.",
                    intent="OCCUPANCY_STATUS",
                    references=refs,
                )
            lines = []
            for card in target_cards:
                names = ", ".join(occ["full_name"] for occ in card["occupants"]) or "Sin personal dentro"
                lines.append(
                    f"{card['code']} ({card['iso_class']}): {card['current_occupancy']}/{card['capacity']} personas ({names})"
                )
            return AgentReply(
                content="Estado actual de ocupación: " + " | ".join(lines) + ".",
                intent="OCCUPANCY_STATUS",
                references=refs,
            )

        # 3. Alerts status
        if any(
            kw in norm
            for kw in ("alerta", "aviso", "incidente", "anomalia", "fuera de rango", "critica", "bloqueo")
        ):
            alert_query = select(Alert).order_by(desc(Alert.created_at), desc(Alert.id)).limit(5)
            if room_ids:
                alert_query = alert_query.where(Alert.cleanroom_id.in_(room_ids))
            recent_alerts = db.scalars(alert_query).all()
            active_count = sum(1 for a in recent_alerts if a.status == "ACTIVE")
            if not recent_alerts:
                return AgentReply(
                    content="No se registran alertas activas ni recientes en las salas consultadas.",
                    intent="ALERTS_STATUS",
                    references=refs,
                )
            room_map = {r.id: r.code for r in scoped_rooms}
            items_txt = [
                f"[{a.status}/{a.severity}] {room_map.get(a.cleanroom_id, f'SALA-{a.cleanroom_id}')}: {a.message}"
                for a in recent_alerts[:3]
            ]
            return AgentReply(
                content=f"Se detectaron {active_count} alertas activas en el resumen reciente. Últimos registros: "
                + " | ".join(items_txt),
                intent="ALERTS_STATUS",
                references=refs,
            )

        # 4. Telemetry / environmental metrics
        if any(
            kw in norm
            for kw in (
                "temperatura",
                "humedad",
                "pm25",
                "pm2.5",
                "particulas",
                "presion",
                "ambiente",
                "telemetria",
                "sensores",
                "lectura",
            )
        ):
            if not target_cards:
                return AgentReply(
                    content="No hay salas disponibles para consultar telemetría.",
                    intent="TELEMETRY_STATUS",
                    references=refs,
                )
            room_summaries = []
            for card in target_cards:
                metric_parts = []
                for m_name, m_data in card["metrics"].items():
                    val_str = f"{m_data['value']} {m_data['unit']}" if m_data["value"] is not None else "sin lectura"
                    state = "OK" if m_data["in_range"] else "FUERA DE RANGO"
                    metric_parts.append(f"{m_name}: {val_str} ({state})")
                joined = ", ".join(metric_parts) if metric_parts else "sin métricas registradas"
                room_summaries.append(f"{card['code']}: {joined}")
            return AgentReply(
                content="Telemetría vigente: " + " | ".join(room_summaries) + ".",
                intent="TELEMETRY_STATUS",
                references=refs,
            )

        # 5. Access history
        if any(
            kw in norm
            for kw in ("acceso", "entrada", "salida", "rechazo", "denegado", "tarjeta", "rfid", "evento", "historial")
        ):
            ev_query = select(AccessEvent).order_by(desc(AccessEvent.occurred_at), desc(AccessEvent.id)).limit(5)
            if room_ids:
                ev_query = ev_query.where(AccessEvent.cleanroom_id.in_(room_ids))
            events = db.scalars(ev_query).all()
            if not events:
                return AgentReply(
                    content="No hay eventos de acceso registrados recientemente en estas salas.",
                    intent="ACCESS_HISTORY",
                    references=refs,
                )
            room_map = {r.id: r.code for r in scoped_rooms}
            ev_txt = [
                f"{room_map.get(ev.cleanroom_id, f'SALA-{ev.cleanroom_id}')} {ev.direction} -> {ev.result}"
                + (f" ({ev.denial_reason})" if ev.denial_reason else "")
                for ev in events[:4]
            ]
            return AgentReply(
                content=(
                    f"Histórico de accesos ({summary['total_events']} totales, {summary['denied']} denegados). "
                    f"Últimos eventos: " + " | ".join(ev_txt)
                ),
                intent="ACCESS_HISTORY",
                references=refs,
            )

        # 6. Autonomous decisions
        if any(kw in norm for kw in ("decision", "bitacora", "regla", "rn-", "autonom")):
            dec_query = (
                select(AgentDecisionLog)
                .order_by(desc(AgentDecisionLog.created_at), desc(AgentDecisionLog.id))
                .limit(5)
            )
            if room_ids:
                dec_query = dec_query.where(AgentDecisionLog.cleanroom_id.in_(room_ids))
            decisions = db.scalars(dec_query).all()
            if not decisions:
                return AgentReply(
                    content="Aún no hay decisiones autónomas registradas en la bitácora del agente.",
                    intent="DECISIONS_SUMMARY",
                    references=refs,
                )
            dec_txt = [f"[{d.rule_code}/{d.decision_type}] {d.summary}" for d in decisions[:4]]
            return AgentReply(
                content="Decisiones recientes del agente: " + " | ".join(dec_txt),
                intent="DECISIONS_SUMMARY",
                references=refs,
            )

        # 7. General operational summary
        decisions_count = (
            db.scalar(
                select(func.count(AgentDecisionLog.id)).where(
                    AgentDecisionLog.cleanroom_id.in_(room_ids) if room_ids else True
                )
            )
            or 0
        )
        return AgentReply(
            content=(
                f"Resumen operativo de {len(summary['rooms'])} salas: "
                f"ocupación total {summary['current_occupancy']} personas, "
                f"{summary['active_alerts']} alertas activas, "
                f"{summary['total_events']} eventos de acceso ({summary['denied']} denegados) "
                f"y {decisions_count} decisiones registradas. "
                "Puedes consultarme sobre ocupación, telemetría, alertas, accesos o pedir bloquear/restablecer una sala."
            ),
            intent="GENERAL_SUMMARY",
            references=refs,
        )


default_agent_provider = DeterministicAgentProvider()


def serialize_decision(item: AgentDecisionLog, room_codes: dict[int, str]) -> dict[str, Any]:
    try:
        details = json.loads(item.details_json or "{}")
    except json.JSONDecodeError:
        details = {}
    return {
        "id": item.id,
        "cleanroom_id": item.cleanroom_id,
        "cleanroom_code": room_codes.get(item.cleanroom_id) if item.cleanroom_id else None,
        "device_id": item.device_id,
        "user_id": item.user_id,
        "rule_code": item.rule_code,
        "decision_type": item.decision_type,
        "severity": item.severity,
        "summary": item.summary,
        "details": details,
        "created_at": as_utc(item.created_at).isoformat(),
    }


def serialize_message(msg: AgentMessage) -> dict[str, Any]:
    try:
        references = json.loads(msg.references_json or "[]")
    except json.JSONDecodeError:
        references = []
    pending_command = None
    if msg.pending_command_json:
        try:
            pending_command = json.loads(msg.pending_command_json)
        except json.JSONDecodeError:
            pending_command = None
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "sender": msg.sender,
        "content": msg.content,
        "intent": msg.intent,
        "references": references,
        "pending_command": pending_command,
        "confirmation_status": msg.confirmation_status,
        "created_at": as_utc(msg.created_at).isoformat(),
    }


def serialize_report(report: AgentReport) -> dict[str, Any]:
    try:
        data = json.loads(report.data_json or "{}")
    except json.JSONDecodeError:
        data = {}
    return {
        "id": report.id,
        "created_by": report.created_by,
        "cleanroom_id": report.cleanroom_id,
        "title": report.title,
        "report_type": report.report_type,
        "summary_text": report.summary_text,
        "data": data,
        "created_at": as_utc(report.created_at).isoformat(),
    }


def build_operational_report(
    db: Session,
    user: User,
    cleanroom_id: int | None = None,
    title: str | None = None,
    report_type: str = "OPERATIONAL_SUMMARY",
) -> AgentReport:
    if cleanroom_id is not None:
        ensure_room_access(user, cleanroom_id)

    summary = get_supervisor_summary(db, user)
    rooms_data = (
        [r for r in summary["rooms"] if r["id"] == cleanroom_id]
        if cleanroom_id is not None
        else summary["rooms"]
    )
    target_ids = [r["id"] for r in rooms_data]

    dec_query = (
        select(AgentDecisionLog)
        .order_by(desc(AgentDecisionLog.created_at), desc(AgentDecisionLog.id))
        .limit(20)
    )
    if target_ids:
        dec_query = dec_query.where(AgentDecisionLog.cleanroom_id.in_(target_ids))
    decisions = db.scalars(dec_query).all()
    room_codes = {r["id"]: r["code"] for r in summary["rooms"]}

    alert_query = (
        select(Alert).order_by(desc(Alert.created_at), desc(Alert.id)).limit(20)
    )
    if target_ids:
        alert_query = alert_query.where(Alert.cleanroom_id.in_(target_ids))
    alerts = db.scalars(alert_query).all()

    scope_label = rooms_data[0]["code"] if cleanroom_id and rooms_data else "Todas las salas"
    report_title = title or f"Reporte Operativo CleanSpace - {scope_label}"
    occ_total = sum(r["current_occupancy"] for r in rooms_data)
    active_alerts_count = sum(1 for a in alerts if a.status == "ACTIVE")

    summary_text = (
        f"Alcance: {scope_label}. "
        f"Salas evaluadas: {len(rooms_data)}. "
        f"Ocupación actual: {occ_total} personas. "
        f"Eventos totales: {summary['total_events']} (entradas: {summary['entries']}, "
        f"salidas: {summary['exits']}, denegados: {summary['denied']}). "
        f"Alertas activas: {active_alerts_count}. "
        f"Decisiones del agente incluidas: {len(decisions)}."
    )

    payload = {
        "generated_at": utcnow().isoformat(),
        "scope": scope_label,
        "kpis": {
            "total_events": summary["total_events"],
            "entries": summary["entries"],
            "exits": summary["exits"],
            "denied": summary["denied"],
            "current_occupancy": occ_total,
            "active_alerts": active_alerts_count,
        },
        "rooms": rooms_data,
        "recent_decisions": [serialize_decision(d, room_codes) for d in decisions],
        "recent_alerts": [
            {
                "id": a.id,
                "cleanroom_id": a.cleanroom_id,
                "type": a.type,
                "severity": a.severity,
                "status": a.status,
                "message": a.message,
                "created_at": as_utc(a.created_at).isoformat(),
            }
            for a in alerts
        ],
    }

    report = AgentReport(
        created_by=user.id,
        cleanroom_id=cleanroom_id,
        title=report_title,
        report_type=report_type,
        summary_text=summary_text,
        data_json=json.dumps(payload, default=str),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def render_report_markdown(report: AgentReport) -> str:
    data = json.loads(report.data_json or "{}")
    kpis = data.get("kpis", {})
    lines = [
        f"# {report.title}",
        "",
        f"- **Fecha de generación:** {as_utc(report.created_at).isoformat()}",
        f"- **Tipo:** {report.report_type}",
        f"- **Resumen:** {report.summary_text}",
        "",
        "## Indicadores Clave",
        "",
        f"- Eventos totales: {kpis.get('total_events', 0)}",
        f"- Entradas: {kpis.get('entries', 0)}",
        f"- Salidas: {kpis.get('exits', 0)}",
        f"- Accesos denegados: {kpis.get('denied', 0)}",
        f"- Ocupación actual: {kpis.get('current_occupancy', 0)}",
        f"- Alertas activas: {kpis.get('active_alerts', 0)}",
        "",
        "## Estado de Salas Limpias",
        "",
    ]
    for room in data.get("rooms", []):
        lines.append(
            f"### {room['code']} - {room['name']} ({room['iso_class']})"
        )
        lines.append(
            f"- Ocupación: {room['current_occupancy']} / {room['capacity']} | Dispositivo: {room.get('device_code') or 'N/A'}"
        )
        for metric_name, m in room.get("metrics", {}).items():
            lines.append(
                f"- {metric_name}: {m.get('value')} {m.get('unit')} (rango {m.get('min_value')} a {m.get('max_value')}, en rango: {m.get('in_range')})"
            )
        lines.append("")

    lines.append("## Decisiones Recientes del Agente")
    lines.append("")
    for d in data.get("recent_decisions", []):
        lines.append(
            f"- [{d['created_at']}] **{d['rule_code']}** ({d['decision_type']}): {d['summary']}"
        )
    return "\n".join(lines)


def render_report_csv(report: AgentReport) -> str:
    data = json.loads(report.data_json or "{}")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "room_code", "key", "value", "status_or_unit", "timestamp"])
    for k, v in data.get("kpis", {}).items():
        writer.writerow(["kpi", "GLOBAL", k, v, "", data.get("generated_at", "")])
    for room in data.get("rooms", []):
        writer.writerow(
            [
                "room_occupancy",
                room["code"],
                "occupancy",
                f"{room['current_occupancy']}/{room['capacity']}",
                room["iso_class"],
                data.get("generated_at", ""),
            ]
        )
        for metric_name, m in room.get("metrics", {}).items():
            writer.writerow(
                [
                    "room_metric",
                    room["code"],
                    metric_name,
                    m.get("value"),
                    f"{m.get('unit')} (in_range={m.get('in_range')})",
                    data.get("generated_at", ""),
                ]
            )
    for d in data.get("recent_decisions", []):
        writer.writerow(
            [
                "agent_decision",
                d.get("cleanroom_code") or "GLOBAL",
                d.get("rule_code"),
                d.get("summary"),
                d.get("decision_type"),
                d.get("created_at"),
            ]
        )
    return buf.getvalue()
