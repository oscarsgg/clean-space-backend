import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import device_from_token, oauth2, output, record_alert, utcnow
from app.core.security import create_token, hash_refresh, hash_rfid
from app.db.database import get_db
from app.models import (
    AccessEvent,
    AccessPermission,
    Command,
    Device,
    MonitoringParameter,
    RfidCard,
    SensorReading,
    User,
)
from app.schemas.access import AccessValidationIn
from app.schemas.iot import DeviceAuthIn, HeartbeatIn, TelemetryBatch
from app.schemas.monitoring import CommandAckIn


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
    created = []
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
            record_alert(
                db,
                device.cleanroom_id,
                device.id,
                reading.id,
                "OUT_OF_RANGE",
                "CRITICAL",
                f"{reading.metric} fuera de rango",
            )
            db.add(Command(device_id=device.id, command="alarma", payload_json=json.dumps({"metric": reading.metric})))
    device.last_seen_at = utcnow()
    db.commit()
    return {"accepted": len(created), "readings": output(created)}


@router.post("/acceso/validar")
def validate_access(
    data: AccessValidationIn,
    token: str = Depends(oauth2),
    db: Session = Depends(get_db),
):
    device = device_from_token(token, db)
    if data.device_id != device.id:
        raise HTTPException(403, "El dispositivo no coincide")
    card = db.scalar(select(RfidCard).where(RfidCard.uid_hash == hash_rfid(data.uid)))
    reason, result, user_id, card_id = None, "DENIED", None, card.id if card else None
    if not card:
        reason = "CARD_NOT_FOUND"
    elif card.status != "ACTIVE" or (card.expires_at and card.expires_at < utcnow()):
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
        elif not permission or (permission.valid_from and permission.valid_from > utcnow()) or (
            permission.valid_until and permission.valid_until < utcnow()
        ):
            reason = "NO_PERMISSION"
        else:
            result, reason = "ALLOWED", None
    event = AccessEvent(
        user_id=user_id,
        cleanroom_id=device.cleanroom_id,
        rfid_card_id=card_id,
        source_device_id=device.id,
        result=result,
        denial_reason=reason,
        direction=data.direction,
    )
    db.add(event)
    db.commit()
    return {"allowed": result == "ALLOWED", "result": result, "reason": reason, "event_id": event.id}


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