import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.dependencies import (
    device_from_token,
    ensure_room_access,
    oauth2,
    output,
    record_alert,
    require,
    utcnow,
)
from app.core.security import create_token, hash_refresh, hash_rfid
from app.db.database import get_db
from app.models import (
    AccessEvent,
    AccessPermission,
    AgentDecisionLog,
    Cleanroom,
    Command,
    Device,
    MonitoringParameter,
    RfidCard,
    SensorReading,
    User,
)
from app.schemas.access import AccessSimulationIn, AccessValidationIn
from app.schemas.iot import DeviceAuthIn, HeartbeatIn, TelemetryBatch
from app.schemas.monitoring import CommandAckIn
from app.services.ecbd import as_utc, compute_room_occupancy
from app.services.realtime import publish_event


router = APIRouter(prefix="/iot", tags=["IoT"])


@router.post("/auth")
def iot_auth(data: DeviceAuthIn, db: Session = Depends(get_db)):
    device = db.scalar(select(Device).where(Device.code == data.code, Device.status == "ACTIVE"))
    if not device or not device.api_key_hash or not secrets.compare_digest(
        device.api_key_hash, hash_refresh(data.api_key)
    ):
        raise HTTPException(401, "Credenciales de dispositivo inválidas")
    token = create_token(str(device.id), "device", timedelta(minutes=60), ["DEVICE"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 3600,
        "device_id": device.id,
        "cleanroom_id": device.cleanroom_id,
    }


@router.post("/heartbeat")
def heartbeat(data: HeartbeatIn, token: str = Depends(oauth2), db: Session = Depends(get_db)):
    device = device_from_token(token, db)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(device, key, value)
    device.last_seen_at = utcnow()
    db.commit()
    return {"status": "ok", "device_id": device.id, "last_seen_at": device.last_seen_at}


@router.post("/telemetria", status_code=201)
def telemetry(data: TelemetryBatch, token: str = Depends(oauth2), db: Session = Depends(get_db)):
    device = device_from_token(token, db)
    if data.device_id != device.id:
        raise HTTPException(403, "El dispositivo no coincide")
    cleanroom = db.get(Cleanroom, device.cleanroom_id)
    room_code = cleanroom.code if cleanroom else f"SALA-{device.cleanroom_id}"

    created = []
    created_alerts = []
    created_commands = []
    created_decisions = []

    for reading_data in data.readings:
        if reading_data.cleanroom_id != device.cleanroom_id or reading_data.device_id != device.id:
            raise HTTPException(400, "Lectura fuera del cuarto del dispositivo")
        reading = SensorReading(
            device_id=device.id,
            cleanroom_id=device.cleanroom_id,
            metric=reading_data.metric,
            value=reading_data.value,
            unit=reading_data.unit,
            recorded_at=reading_data.recorded_at or utcnow(),
        )
        db.add(reading)
        db.flush()
        created.append(reading)
        parameter = db.scalar(
            select(MonitoringParameter).where(
                MonitoringParameter.cleanroom_id == device.cleanroom_id,
                MonitoringParameter.metric == reading.metric,
                MonitoringParameter.enabled.is_(True),
            )
        )
        if parameter and not parameter.min_value <= reading.value <= parameter.max_value:
            alert = record_alert(
                db,
                device.cleanroom_id,
                device.id,
                reading.id,
                "OUT_OF_RANGE",
                "CRITICAL",
                f"{reading.metric} fuera de rango en {room_code} ({reading.value} {reading.unit})",
            )
            cmd = Command(
                device_id=device.id,
                command="alarma",
                payload_json=json.dumps({"metric": reading.metric, "value": reading.value}),
            )
            decision = AgentDecisionLog(
                cleanroom_id=device.cleanroom_id,
                device_id=device.id,
                rule_code="RN-11",
                decision_type="ALERT",
                severity="CRITICAL",
                summary=f"Anomalía en {room_code}: {reading.metric}={reading.value}{reading.unit} fuera de [{parameter.min_value}, {parameter.max_value}]",
                details_json=json.dumps(
                    {
                        "reading_id": reading.id,
                        "metric": reading.metric,
                        "value": reading.value,
                        "unit": reading.unit,
                        "min_value": parameter.min_value,
                        "max_value": parameter.max_value,
                    }
                ),
            )
            db.add(cmd)
            db.add(decision)
            db.flush()
            created_alerts.append(alert)
            created_commands.append(cmd)
            created_decisions.append(decision)

    device.last_seen_at = utcnow()
    db.commit()

    for item in created:
        publish_event("telemetry_reading", output(item))
    for item in created_alerts:
        publish_event("alert_created", output(item))
    for item in created_commands:
        publish_event("command_created", output(item))
    for item in created_decisions:
        publish_event("agent_decision", output(item))

    return {"accepted": len(created), "readings": output(created)}


def process_access_validation(
    db: Session,
    device: Device,
    uid: str,
    direction: str = "ENTRY",
) -> dict:
    normalized_direction = direction.upper() if direction else "ENTRY"
    cleanroom = db.get(Cleanroom, device.cleanroom_id)
    room_code = cleanroom.code if cleanroom else f"SALA-{device.cleanroom_id}"
    room_capacity = cleanroom.capacity if cleanroom else 8

    card = db.scalar(select(RfidCard).where(RfidCard.uid_hash == hash_rfid(uid)))
    reason, result, user_id, card_id = None, "DENIED", None, card.id if card else None
    now_dt = utcnow()

    if not card:
        reason = "CARD_NOT_FOUND"
    elif card.status != "ACTIVE" or (card.expires_at and as_utc(card.expires_at) < now_dt):
        reason = "CARD_INACTIVE"
    else:
        person = db.get(User, card.user_id)
        user_id = person.id if person else None
        permission = db.scalar(
            select(AccessPermission).where(
                AccessPermission.user_id == card.user_id,
                AccessPermission.cleanroom_id == device.cleanroom_id,
                AccessPermission.enabled.is_(True),
            )
        )
        if not person or person.status != "ACTIVE":
            reason = "PERSON_INACTIVE"
        elif (
            not permission
            or (permission.valid_from and as_utc(permission.valid_from) > now_dt)
            or (permission.valid_until and as_utc(permission.valid_until) < now_dt)
        ):
            reason = "NO_PERMISSION"
        else:
            result, reason = "ALLOWED", None

    inside_map = compute_room_occupancy(db, device.cleanroom_id)
    current_occupancy = len(inside_map)

    if result == "ALLOWED" and normalized_direction == "ENTRY" and current_occupancy >= room_capacity:
        result = "DENIED"
        reason = "ROOM_FULL"

    stay_minutes: float | None = None
    if result == "ALLOWED" and normalized_direction == "EXIT" and user_id is not None:
        last_entry = db.scalar(
            select(AccessEvent)
            .where(
                AccessEvent.cleanroom_id == device.cleanroom_id,
                AccessEvent.user_id == user_id,
                AccessEvent.result == "ALLOWED",
                AccessEvent.direction == "ENTRY",
            )
            .order_by(desc(AccessEvent.occurred_at), desc(AccessEvent.id))
            .limit(1)
        )
        if last_entry:
            elapsed = (as_utc(now_dt) - as_utc(last_entry.occurred_at)).total_seconds()
            stay_minutes = round(max(0.0, elapsed / 60.0), 2)

    event = AccessEvent(
        user_id=user_id,
        cleanroom_id=device.cleanroom_id,
        rfid_card_id=card_id,
        source_device_id=device.id,
        result=result,
        denial_reason=reason,
        direction=normalized_direction,
        occurred_at=now_dt,
    )
    db.add(event)
    db.flush()

    if result == "ALLOWED":
        if normalized_direction == "ENTRY" and user_id is not None:
            inside_map[user_id] = event
        elif normalized_direction == "EXIT" and user_id is not None:
            inside_map.pop(user_id, None)
        new_occupancy = len(inside_map)
        stay_note = f" (permanencia {stay_minutes} min)" if stay_minutes is not None else ""
        decision = AgentDecisionLog(
            cleanroom_id=device.cleanroom_id,
            device_id=device.id,
            user_id=user_id,
            rule_code="RN-08",
            decision_type="ALLOW",
            severity="INFO",
            summary=f"Acceso {normalized_direction} permitido en {room_code} (aforo {new_occupancy}/{room_capacity}){stay_note}",
            details_json=json.dumps(
                {
                    "event_id": event.id,
                    "direction": normalized_direction,
                    "occupancy": new_occupancy,
                    "capacity": room_capacity,
                    "stay_minutes": stay_minutes,
                }
            ),
        )
    else:
        new_occupancy = current_occupancy
        decision = AgentDecisionLog(
            cleanroom_id=device.cleanroom_id,
            device_id=device.id,
            user_id=user_id,
            rule_code="RN-03",
            decision_type="DENY",
            severity="WARNING",
            summary=f"Acceso {normalized_direction} denegado en {room_code}: {reason}",
            details_json=json.dumps(
                {
                    "event_id": event.id,
                    "direction": normalized_direction,
                    "reason": reason,
                    "occupancy": current_occupancy,
                    "capacity": room_capacity,
                }
            ),
        )
    db.add(decision)
    db.flush()

    lock_alert = None
    lock_cmd = None
    lock_decision = None
    auto_locked = False

    if result == "DENIED":
        recent_events = db.scalars(
            select(AccessEvent)
            .where(AccessEvent.source_device_id == device.id)
            .order_by(desc(AccessEvent.occurred_at), desc(AccessEvent.id))
            .limit(3)
        ).all()
        if (
            len(recent_events) == 3
            and all(item.result == "DENIED" for item in recent_events)
            and (as_utc(recent_events[0].occurred_at) - as_utc(recent_events[2].occurred_at))
            <= timedelta(minutes=5)
        ):
            auto_locked = True
            lock_alert = record_alert(
                db,
                device.cleanroom_id,
                device.id,
                None,
                "SECURITY_LOCKOUT",
                "CRITICAL",
                f"Bloqueo automático en {room_code}: 3 rechazos consecutivos en menos de 5 minutos",
            )
            lock_cmd = Command(
                device_id=device.id,
                command="bloquear",
                payload_json=json.dumps(
                    {
                        "reason": "CONSECUTIVE_DENIED",
                        "duration_seconds": 300,
                        "cleanroom_id": device.cleanroom_id,
                    }
                ),
            )
            lock_decision = AgentDecisionLog(
                cleanroom_id=device.cleanroom_id,
                device_id=device.id,
                user_id=user_id,
                rule_code="RN-10",
                decision_type="LOCK",
                severity="CRITICAL",
                summary=f"Bloqueo automático activado en {room_code} tras 3 rechazos consecutivos",
                details_json=json.dumps(
                    {
                        "event_ids": [item.id for item in recent_events],
                        "window_minutes": 5,
                    }
                ),
            )
            db.add(lock_cmd)
            db.add(lock_decision)
            db.flush()

    db.commit()
    db.refresh(event)
    db.refresh(decision)

    publish_event("access_event", output(event))
    publish_event("agent_decision", output(decision))
    if auto_locked:
        if lock_alert:
            db.refresh(lock_alert)
            publish_event("alert_created", output(lock_alert))
        if lock_cmd:
            db.refresh(lock_cmd)
            publish_event("command_created", output(lock_cmd))
        if lock_decision:
            db.refresh(lock_decision)
            publish_event("agent_decision", output(lock_decision))

    return {
        "allowed": result == "ALLOWED",
        "result": result,
        "reason": reason,
        "event_id": event.id,
        "stay_minutes": stay_minutes,
        "current_occupancy": new_occupancy,
        "capacity": room_capacity,
        "auto_locked": auto_locked,
    }


@router.post("/acceso/validar")
def validate_access(
    data: AccessValidationIn,
    token: str = Depends(oauth2),
    db: Session = Depends(get_db),
):
    device = device_from_token(token, db)
    if data.device_id != device.id:
        raise HTTPException(403, "El dispositivo no coincide")
    return process_access_validation(db, device, data.uid, data.direction)


@router.post("/simular-acceso")
def simulate_access(
    data: AccessSimulationIn,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    ensure_room_access(user, data.cleanroom_id)
    if not db.get(Cleanroom, data.cleanroom_id):
        raise HTTPException(404, "Cuarto no encontrado")
    if data.device_id is not None:
        device = db.get(Device, data.device_id)
        if not device or device.cleanroom_id != data.cleanroom_id:
            raise HTTPException(404, "Dispositivo no encontrado para el cuarto indicado")
    else:
        device = db.scalar(
            select(Device)
            .where(Device.cleanroom_id == data.cleanroom_id, Device.status == "ACTIVE")
            .order_by(Device.id.asc())
        )
        if not device:
            device = db.scalar(
                select(Device)
                .where(Device.cleanroom_id == data.cleanroom_id)
                .order_by(Device.id.asc())
            )
        if not device:
            raise HTTPException(404, "El cuarto no tiene dispositivos configurados")
    return process_access_validation(db, device, data.uid, data.direction)


@router.get("/comandos/pendientes")
def pending_commands(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    device = device_from_token(token, db)
    commands = db.scalars(
        select(Command)
        .where(Command.device_id == device.id, Command.status == "PENDING")
        .order_by(Command.created_at)
    ).all()
    for command in commands:
        command.status = "SENT"
        command.sent_at = utcnow()
    db.commit()
    return output(commands)


@router.post("/comandos/{id}/ack")
def acknowledge_command(
    id: int,
    data: CommandAckIn,
    token: str = Depends(oauth2),
    db: Session = Depends(get_db),
):
    device = device_from_token(token, db)
    command = db.get(Command, id)
    if not command or command.device_id != device.id:
        raise HTTPException(404, "Comando no encontrado")
    command.status = data.status
    command.acknowledged_at = utcnow()
    db.commit()
    return output(command)