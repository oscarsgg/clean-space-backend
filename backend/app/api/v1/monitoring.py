import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.api.v1.iot import telemetry
from app.core.dependencies import current_user, ensure_room_access, output, require, utcnow
from app.core.security import create_token
from app.db.database import get_db
from app.models import AccessEvent, Alert, Command, Device, SensorReading
from app.schemas.iot import TelemetryBatch
from app.schemas.monitoring import CommandIn, ReadingIn


router = APIRouter(tags=["Monitoring"])


@router.get("/comandos")
def commands(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    query = select(Command).order_by(desc(Command.created_at)).limit(200)
    return output(db.scalars(query).all())


@router.post("/comandos", status_code=201)
def create_command(
    data: CommandIn,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    if not db.get(Device, data.device_id):
        raise HTTPException(404, "Dispositivo no encontrado")
    if data.command not in {"alarma", "indicador", "bloquear", "normal", "leer_ahora"}:
        raise HTTPException(422, "Comando no permitido")
    item = Command(device_id=data.device_id, command=data.command, payload_json=json.dumps(data.payload))
    db.add(item)
    db.commit()
    db.refresh(item)
    return output(item)


@router.post("/monitoreo/lecturas", status_code=201)
def add_reading(
    data: ReadingIn,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    ensure_room_access(user, data.cleanroom_id)
    batch = TelemetryBatch(device_id=data.device_id, readings=[data])
    token = create_token(str(data.device_id), "device", timedelta(minutes=1), ["DEVICE"])
    return telemetry(batch, token, db)


@router.get("/monitoreo/historico")
def readings(
    page: int = 1,
    size: int = Query(200, le=500),
    cleanroom_id: int | None = None,
    metric: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(current_user),
):
    query = (
        select(SensorReading)
        .order_by(desc(SensorReading.recorded_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    if cleanroom_id is not None:
        ensure_room_access(user, cleanroom_id)
        query = query.where(SensorReading.cleanroom_id == cleanroom_id)
    return output(db.scalars(query).all())


@router.get("/accesos")
def access_events(
    page: int = 1,
    size: int = Query(100, le=500),
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    query = (
        select(AccessEvent)
        .order_by(desc(AccessEvent.occurred_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    return output(db.scalars(query).all())


@router.get("/avisos")
def alerts(db: Session = Depends(get_db), user=Depends(current_user)):
    query = select(Alert).order_by(desc(Alert.created_at)).limit(100)
    return output(db.scalars(query).all())


@router.patch("/avisos/{id}/acknowledge")
def acknowledge(
    id: int,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    item = db.get(Alert, id)
    if not item:
        raise HTTPException(404, "Aviso no encontrado")
    item.status = "ACKNOWLEDGED"
    item.acknowledged_by = user.id
    item.acknowledged_at = utcnow()
    if item.device_id:
        db.add(Command(device_id=item.device_id, command="normal", payload_json="{}"))
    db.commit()
    return output(item)