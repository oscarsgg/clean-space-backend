import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_token, decode_token, hash_password, hash_refresh, hash_rfid, verify_password
from app.db.session import Base, engine, get_db
from app.models.models import *

settings = get_settings()
Base.metadata.create_all(bind=engine)
app = FastAPI(title=settings.app_name, version="2.0.0", description="API REST e IoT de CleanSpace")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class RefreshIn(BaseModel):
    refresh_token: str | None = None

class PersonIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)
    full_name: str = Field(min_length=2, max_length=150)
    role_codes: list[str] = ["PERSONAL_AUTORIZADO"]

class PersonUpdate(BaseModel):
    full_name: str | None = None
    status: str | None = None
    role_codes: list[str] | None = None

class RoomIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=120)
    location: str = ""
    description: str = ""
    access_level: str = "STANDARD"
    status: str = "ACTIVE"

class RoomUpdate(BaseModel):
    name: str | None = None
    location: str | None = None
    description: str | None = None
    access_level: str | None = None
    status: str | None = None

class DeviceIn(BaseModel):
    cleanroom_id: int
    code: str
    type: str
    serial_number: str

class DeviceUpdate(BaseModel):
    cleanroom_id: int | None = None
    type: str | None = None
    status: str | None = None
    firmware_version: str | None = None

class CardIn(BaseModel):
    user_id: int
    uid: str = Field(min_length=4, max_length=64)
    label: str = ""
    expires_at: datetime | None = None

class CardUpdate(BaseModel):
    status: str | None = None
    label: str | None = None
    expires_at: datetime | None = None

class PermissionIn(BaseModel):
    user_id: int
    cleanroom_id: int
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    enabled: bool = True

class PermissionUpdate(BaseModel):
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    enabled: bool | None = None

class ParameterIn(BaseModel):
    metric: str
    min_value: float
    max_value: float
    unit: str
    enabled: bool = True

class ReadingIn(BaseModel):
    device_id: int
    cleanroom_id: int
    metric: str
    value: float
    unit: str
    recorded_at: datetime | None = None

class TelemetryBatch(BaseModel):
    device_id: int
    readings: list[ReadingIn] = Field(min_length=1, max_length=100)

class DeviceAuthIn(BaseModel):
    code: str
    api_key: str

class HeartbeatIn(BaseModel):
    firmware_version: str | None = None
    rssi: int | None = None
    free_memory: int | None = None

class AccessValidationIn(BaseModel):
    device_id: int
    uid: str
    direction: str = "ENTRY"

class CommandIn(BaseModel):
    device_id: int
    command: str
    payload: dict[str, Any] = {}

class CommandAckIn(BaseModel):
    status: str = "ACKNOWLEDGED"


def utcnow():
    return datetime.now(timezone.utc)


def output(obj):
    if isinstance(obj, list):
        return [output(item) for item in obj]
    if isinstance(obj, (User, Role, Cleanroom, Device, RfidCard, AccessPermission, AccessEvent, SensorReading, MonitoringParameter, Alert, Command, ModelResult)):
        data = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        return data
    return obj


def user_out(user):
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "status": user.status, "roles": [r.code for r in user.roles]}


def current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    payload = decode_token(token, "access")
    user = db.get(User, int(payload["sub"])) if payload else None
    if not user or user.status != "ACTIVE":
        raise HTTPException(401, "Credenciales inválidas")
    return user


def require(*roles):
    def dependency(user=Depends(current_user)):
        if not any(role.code in roles for role in user.roles):
            raise HTTPException(403, "Permisos insuficientes")
        return user
    return dependency


def device_from_token(token: str, db: Session):
    payload = decode_token(token, "device")
    device = db.get(Device, int(payload["sub"])) if payload else None
    if not device or device.status != "ACTIVE":
        raise HTTPException(401, "Credenciales de dispositivo inválidas")
    return device


def issue_tokens(db, user):
    access = create_token(str(user.id), "access", timedelta(minutes=settings.access_token_expire_minutes), [r.code for r in user.roles])
    refresh = secrets.token_urlsafe(48)
    db.add(RefreshSession(user_id=user.id, token_hash=hash_refresh(refresh), expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days)))
    db.commit()
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "expires_in": settings.access_token_expire_minutes * 60, "user": user_out(user)}


def record_alert(db, room_id, device_id, reading_id, alert_type, severity, message):
    alert = Alert(cleanroom_id=room_id, device_id=device_id, reading_id=reading_id, type=alert_type, severity=severity, message=message)
    db.add(alert)
    return alert


def ensure_room_access(user, room_id):
    role_codes = {r.code for r in user.roles}
    if "ADMINISTRADOR" in role_codes:
        return
    if room_id not in {room.id for room in user.assigned_cleanrooms}:
        raise HTTPException(403, "No tienes acceso a este cuarto")

@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name, "version": "2.0.0"}

@app.post("/api/v1/auth/login")
async def login(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        email, password = data.get("email"), data.get("password")
    else:
        form = await request.form()
        email, password = form.get("username"), form.get("password")
    user = db.scalar(select(User).where(User.email == str(email).lower().strip())) if email else None
    if not user or not password or not verify_password(password, user.password_hash) or user.status != "ACTIVE":
        raise HTTPException(401, "Credenciales inválidas")
    return issue_tokens(db, user)

@app.post("/api/v1/auth/refresh")
def refresh(data: RefreshIn, response: Response, db: Session = Depends(get_db)):
    token = data.refresh_token
    session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == hash_refresh(token or "")))
    if not session or session.revoked_at or session.expires_at < utcnow():
        raise HTTPException(401, "Refresh token inválido")
    session.revoked_at = utcnow()
    user = db.get(User, session.user_id)
    db.commit()
    result = issue_tokens(db, user)
    response.set_cookie("refresh_token", result["refresh_token"], httponly=True, secure=False, samesite="lax", max_age=settings.refresh_token_expire_days * 86400)
    return result

@app.post("/api/v1/auth/logout", status_code=204)
def logout(data: RefreshIn, response: Response, db: Session = Depends(get_db), user=Depends(current_user)):
    session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == hash_refresh(data.refresh_token or ""), RefreshSession.user_id == user.id))
    if session:
        session.revoked_at = utcnow()
        db.commit()
    response.delete_cookie("refresh_token")

@app.get("/api/v1/auth/me")
def me(user=Depends(current_user)): return user_out(user)

@app.get("/api/v1/roles")
def roles(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    return output(db.scalars(select(Role)).all())

@app.get("/api/v1/personas")
def people(page: int = 1, size: int = Query(50, le=100), status_filter: str | None = None, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    query = select(User).offset((page - 1) * size).limit(size)
    if status_filter: query = query.where(User.status == status_filter)
    return [user_out(item) for item in db.scalars(query).all()]

@app.post("/api/v1/personas", status_code=201)
def create_person(data: PersonIn, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    if db.scalar(select(User).where(User.email == data.email.lower())): raise HTTPException(409, "El correo ya existe")
    roles = list(db.scalars(select(Role).where(Role.code.in_(data.role_codes))).all())
    if len(roles) != len(set(data.role_codes)): raise HTTPException(400, "Uno o más roles no existen")
    item = User(email=data.email.lower(), password_hash=hash_password(data.password), full_name=data.full_name, roles=roles)
    db.add(item); db.commit(); db.refresh(item); return user_out(item)

@app.put("/api/v1/personas/{id}")
@app.patch("/api/v1/personas/{id}")
def update_person(id: int, data: PersonUpdate, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    item = db.get(User, id)
    if not item: raise HTTPException(404, "Persona no encontrada")
    if data.full_name is not None: item.full_name = data.full_name
    if data.status is not None: item.status = data.status
    if data.role_codes is not None: item.roles = list(db.scalars(select(Role).where(Role.code.in_(data.role_codes))).all())
    db.commit(); db.refresh(item); return user_out(item)

@app.delete("/api/v1/personas/{id}", status_code=204)
def delete_person(id: int, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    item = db.get(User, id)
    if not item: raise HTTPException(404, "Persona no encontrada")
    item.status = "INACTIVE"; db.commit()

@app.get("/api/v1/cuartos")
def rooms(db: Session = Depends(get_db), user=Depends(current_user)):
    items = db.scalars(select(Cleanroom)).all()
    if "ADMINISTRADOR" not in {r.code for r in user.roles}: items = [r for r in items if r in user.assigned_cleanrooms]
    return output(items)

@app.post("/api/v1/cuartos", status_code=201)
def create_room(data: RoomIn, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    if db.scalar(select(Cleanroom).where(Cleanroom.code == data.code)): raise HTTPException(409, "Código duplicado")
    item = Cleanroom(**data.model_dump()); db.add(item); db.commit(); db.refresh(item); return output(item)

@app.put("/api/v1/cuartos/{id}")
@app.patch("/api/v1/cuartos/{id}")
def update_room(id: int, data: RoomUpdate, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    item = db.get(Cleanroom, id)
    if not item: raise HTTPException(404, "Cuarto no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items(): setattr(item, key, value)
    db.commit(); db.refresh(item); return output(item)

@app.delete("/api/v1/cuartos/{id}", status_code=204)
def delete_room(id: int, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    item = db.get(Cleanroom, id)
    if not item: raise HTTPException(404, "Cuarto no encontrado")
    item.status = "INACTIVE"; db.commit()

@app.get("/api/v1/cuartos/{id}/parametros")
def room_parameters(id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    ensure_room_access(user, id); return output(db.scalars(select(MonitoringParameter).where(MonitoringParameter.cleanroom_id == id)).all())

@app.put("/api/v1/cuartos/{id}/parametros")
def set_room_parameters(id: int, data: list[ParameterIn], db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    if not db.get(Cleanroom, id): raise HTTPException(404, "Cuarto no encontrado")
    for param in data:
        item = db.scalar(select(MonitoringParameter).where(MonitoringParameter.cleanroom_id == id, MonitoringParameter.metric == param.metric))
        if not item: item = MonitoringParameter(cleanroom_id=id, metric=param.metric); db.add(item)
        for key, value in param.model_dump().items(): setattr(item, key, value)
    db.commit(); return output(db.scalars(select(MonitoringParameter).where(MonitoringParameter.cleanroom_id == id)).all())

@app.get("/api/v1/dispositivos")
def devices(db: Session = Depends(get_db), user=Depends(current_user)):
    items = db.scalars(select(Device)).all()
    if "ADMINISTRADOR" not in {r.code for r in user.roles}: items = [d for d in items if d.cleanroom_id in {r.id for r in user.assigned_cleanrooms}]
    return output(items)

@app.get("/api/v1/dispositivos/{id}")
def get_device(id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    item = db.get(Device, id)
    if not item: raise HTTPException(404, "Dispositivo no encontrado")
    ensure_room_access(user, item.cleanroom_id); return output(item)

@app.post("/api/v1/dispositivos", status_code=201)
def create_device(data: DeviceIn, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    if not db.get(Cleanroom, data.cleanroom_id): raise HTTPException(400, "Cuarto inválido")
    item = Device(**data.model_dump()); api_key = secrets.token_urlsafe(32); item.api_key_hash = hash_refresh(api_key)
    db.add(item); db.commit(); db.refresh(item)
    return {**output(item), "api_key": api_key}

@app.put("/api/v1/dispositivos/{id}")
@app.patch("/api/v1/dispositivos/{id}")
def update_device(id: int, data: DeviceUpdate, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    item = db.get(Device, id)
    if not item: raise HTTPException(404, "Dispositivo no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items(): setattr(item, key, value)
    db.commit(); db.refresh(item); return output(item)

@app.delete("/api/v1/dispositivos/{id}", status_code=204)
def delete_device(id: int, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    item = db.get(Device, id)
    if not item: raise HTTPException(404, "Dispositivo no encontrado")
    item.status = "INACTIVE"; db.commit()

@app.post("/api/v1/dispositivos/{id}/llave")
def regenerate_device_key(id: int, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    item = db.get(Device, id)
    if not item: raise HTTPException(404, "Dispositivo no encontrado")
    api_key = secrets.token_urlsafe(32); item.api_key_hash = hash_refresh(api_key); db.commit(); return {"device_id": id, "api_key": api_key}

@app.get("/api/v1/tarjetas")
def cards(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))): return output(db.scalars(select(RfidCard)).all())

@app.post("/api/v1/personas/{id}/tarjetas", status_code=201)
def add_card(id: int, data: CardIn, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    if id != data.user_id or not db.get(User, id): raise HTTPException(400, "Persona inválida")
    item = RfidCard(user_id=id, uid_hash=hash_rfid(data.uid), label=data.label, expires_at=data.expires_at); db.add(item); db.commit(); db.refresh(item); return output(item)

@app.patch("/api/v1/tarjetas/{id}")
def update_card(id: int, data: CardUpdate, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    item = db.get(RfidCard, id)
    if not item: raise HTTPException(404, "Tarjeta no encontrada")
    for key, value in data.model_dump(exclude_unset=True).items(): setattr(item, key, value)
    db.commit(); return output(item)

@app.get("/api/v1/permisos")
def permissions(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))): return output(db.scalars(select(AccessPermission)).all())

@app.post("/api/v1/permisos", status_code=201)
def create_permission(data: PermissionIn, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    if not db.get(User, data.user_id) or not db.get(Cleanroom, data.cleanroom_id): raise HTTPException(400, "Persona o cuarto inválido")
    item = AccessPermission(**data.model_dump()); db.add(item); db.commit(); db.refresh(item); return output(item)

@app.patch("/api/v1/permisos/{id}")
def update_permission(id: int, data: PermissionUpdate, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    item = db.get(AccessPermission, id)
    if not item: raise HTTPException(404, "Permiso no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items(): setattr(item, key, value)
    db.commit(); return output(item)

@app.post("/api/v1/iot/auth")
def iot_auth(data: DeviceAuthIn, db: Session = Depends(get_db)):
    device = db.scalar(select(Device).where(Device.code == data.code, Device.status == "ACTIVE"))
    if not device or not device.api_key_hash or not secrets.compare_digest(device.api_key_hash, hash_refresh(data.api_key)): raise HTTPException(401, "Credenciales de dispositivo inválidas")
    token = create_token(str(device.id), "device", timedelta(minutes=60), ["DEVICE"])
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600, "device_id": device.id, "cleanroom_id": device.cleanroom_id}

@app.post("/api/v1/iot/heartbeat")
def heartbeat(data: HeartbeatIn, token: str = Depends(oauth2), db: Session = Depends(get_db)):
    device = device_from_token(token, db)
    for key, value in data.model_dump(exclude_unset=True).items(): setattr(device, key, value)
    device.last_seen_at = utcnow(); db.commit(); return {"status": "ok", "device_id": device.id, "last_seen_at": device.last_seen_at}

@app.post("/api/v1/iot/telemetria", status_code=201)
def telemetry(data: TelemetryBatch, token: str = Depends(oauth2), db: Session = Depends(get_db)):
    device = device_from_token(token, db)
    if data.device_id != device.id: raise HTTPException(403, "El dispositivo no coincide")
    created = []
    for reading_data in data.readings:
        if reading_data.cleanroom_id != device.cleanroom_id or reading_data.device_id != device.id: raise HTTPException(400, "Lectura fuera del cuarto del dispositivo")
        reading = SensorReading(device_id=device.id, cleanroom_id=device.cleanroom_id, metric=reading_data.metric, value=reading_data.value, unit=reading_data.unit, recorded_at=reading_data.recorded_at or utcnow())
        db.add(reading); db.flush(); created.append(reading)
        parameter = db.scalar(select(MonitoringParameter).where(MonitoringParameter.cleanroom_id == device.cleanroom_id, MonitoringParameter.metric == reading.metric, MonitoringParameter.enabled == True))
        if parameter and not parameter.min_value <= reading.value <= parameter.max_value:
            record_alert(db, device.cleanroom_id, device.id, reading.id, "OUT_OF_RANGE", "CRITICAL", f"{reading.metric} fuera de rango")
            db.add(Command(device_id=device.id, command="alarma", payload_json=json.dumps({"metric": reading.metric})))
    device.last_seen_at = utcnow(); db.commit(); return {"accepted": len(created), "readings": output(created)}

@app.post("/api/v1/iot/acceso/validar")
def validate_access(data: AccessValidationIn, token: str = Depends(oauth2), db: Session = Depends(get_db)):
    device = device_from_token(token, db)
    if data.device_id != device.id: raise HTTPException(403, "El dispositivo no coincide")
    card = db.scalar(select(RfidCard).where(RfidCard.uid_hash == hash_rfid(data.uid)))
    reason, result, user_id, card_id = None, "DENIED", None, card.id if card else None
    if not card: reason = "CARD_NOT_FOUND"
    elif card.status != "ACTIVE" or (card.expires_at and card.expires_at < utcnow()): reason = "CARD_INACTIVE"
    else:
        person = db.get(User, card.user_id); user_id = person.id if person else None
        permission = db.scalar(select(AccessPermission).where(AccessPermission.user_id == card.user_id, AccessPermission.cleanroom_id == device.cleanroom_id, AccessPermission.enabled == True))
        if not person or person.status != "ACTIVE": reason = "PERSON_INACTIVE"
        elif not permission or (permission.valid_from and permission.valid_from > utcnow()) or (permission.valid_until and permission.valid_until < utcnow()): reason = "NO_PERMISSION"
        else: result, reason = "ALLOWED", None
    event = AccessEvent(user_id=user_id, cleanroom_id=device.cleanroom_id, rfid_card_id=card_id, source_device_id=device.id, result=result, denial_reason=reason, direction=data.direction)
    db.add(event); db.commit()
    return {"allowed": result == "ALLOWED", "result": result, "reason": reason, "event_id": event.id}

@app.get("/api/v1/iot/comandos/pendientes")
def pending_commands(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    device = device_from_token(token, db); commands = db.scalars(select(Command).where(Command.device_id == device.id, Command.status == "PENDING").order_by(Command.created_at)).all()
    for command in commands: command.status = "SENT"; command.sent_at = utcnow()
    db.commit(); return output(commands)

@app.post("/api/v1/iot/comandos/{id}/ack")
def acknowledge_command(id: int, data: CommandAckIn, token: str = Depends(oauth2), db: Session = Depends(get_db)):
    device = device_from_token(token, db); command = db.get(Command, id)
    if not command or command.device_id != device.id: raise HTTPException(404, "Comando no encontrado")
    command.status = data.status; command.acknowledged_at = utcnow(); db.commit(); return output(command)

@app.get("/api/v1/comandos")
def commands(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))): return output(db.scalars(select(Command).order_by(desc(Command.created_at)).limit(200)).all())

@app.post("/api/v1/comandos", status_code=201)
def create_command(data: CommandIn, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    if not db.get(Device, data.device_id): raise HTTPException(404, "Dispositivo no encontrado")
    if data.command not in {"alarma", "indicador", "bloquear", "normal", "leer_ahora"}: raise HTTPException(422, "Comando no permitido")
    item = Command(device_id=data.device_id, command=data.command, payload_json=json.dumps(data.payload)); db.add(item); db.commit(); db.refresh(item); return output(item)

@app.post("/api/v1/monitoreo/lecturas", status_code=201)
def add_reading(data: ReadingIn, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    ensure_room_access(user, data.cleanroom_id)
    return telemetry(TelemetryBatch(device_id=data.device_id, readings=[data]), create_token(str(data.device_id), "device", timedelta(minutes=1), ["DEVICE"]), db)

@app.get("/api/v1/monitoreo/historico")
def readings(page: int = 1, size: int = Query(200, le=500), cleanroom_id: int | None = None, metric: str | None = None, db: Session = Depends(get_db), user=Depends(current_user)):
    query = select(SensorReading).order_by(desc(SensorReading.recorded_at)).offset((page - 1) * size).limit(size)
    if cleanroom_id is not None: ensure_room_access(user, cleanroom_id); query = query.where(SensorReading.cleanroom_id == cleanroom_id)
    return output(db.scalars(query).all())

@app.get("/api/v1/accesos")
def access_events(page: int = 1, size: int = Query(100, le=500), db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))): return output(db.scalars(select(AccessEvent).order_by(desc(AccessEvent.occurred_at)).offset((page - 1) * size).limit(size)).all())

@app.get("/api/v1/avisos")
def alerts(db: Session = Depends(get_db), user=Depends(current_user)): return output(db.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(100)).all())

@app.patch("/api/v1/avisos/{id}/acknowledge")
def acknowledge(id: int, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    item = db.get(Alert, id)
    if not item: raise HTTPException(404, "Aviso no encontrado")
    item.status = "ACKNOWLEDGED"; item.acknowledged_by = user.id; item.acknowledged_at = utcnow()
    if item.device_id: db.add(Command(device_id=item.device_id, command="normal", payload_json="{}"))
    db.commit(); return output(item)

@app.post("/api/v1/analitica/etl/ejecutar")
def run_etl(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    return {"status": "completed", "processed_readings": db.scalar(select(func.count(SensorReading.id))) or 0, "processed_accesses": db.scalar(select(func.count(AccessEvent.id))) or 0, "executed_at": utcnow()}

@app.post("/api/v1/analitica/modelo/entrenar")
def train_model(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    readings_count = db.scalar(select(func.count(SensorReading.id))) or 0
    result = ModelResult(model_type="isolation_forest", result_json=json.dumps({"training_rows": readings_count, "status": "trained"}))
    db.add(result); db.commit(); db.refresh(result); return output(result)

@app.get("/api/v1/analitica/modelo/metricas")
def model_metrics(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))): return output(db.scalars(select(ModelResult).order_by(desc(ModelResult.trained_at)).limit(50)).all())

@app.get("/api/v1/analitica/anomalias")
def anomalies(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))): return output(db.scalars(select(Alert).where(Alert.type == "OUT_OF_RANGE").order_by(desc(Alert.created_at)).limit(200)).all())

@app.get("/api/v1/analitica/indicadores")
def indicators(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    return {"total_readings": db.scalar(select(func.count(SensorReading.id))) or 0, "total_accesses": db.scalar(select(func.count(AccessEvent.id))) or 0, "allowed_accesses": db.scalar(select(func.count(AccessEvent.id)).where(AccessEvent.result == "ALLOWED")) or 0, "denied_accesses": db.scalar(select(func.count(AccessEvent.id)).where(AccessEvent.result == "DENIED")) or 0, "active_alerts": db.scalar(select(func.count(Alert.id)).where(Alert.status == "ACTIVE")) or 0}

@app.get("/api/v1/analitica/series")
def series(cleanroom_id: int | None = None, metric: str | None = None, db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    query = select(SensorReading).order_by(SensorReading.recorded_at)
    if cleanroom_id: query = query.where(SensorReading.cleanroom_id == cleanroom_id)
    if metric: query = query.where(SensorReading.metric == metric)
    return output(db.scalars(query.limit(1000)).all())

@app.get("/api/v1/analitica/patrones-acceso")
def access_patterns(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    rows = db.execute(select(func.strftime("%H", AccessEvent.occurred_at).label("hour"), func.count(AccessEvent.id)).group_by("hour").order_by("hour")).all()
    return [{"hour": hour, "count": count} for hour, count in rows]

@app.get("/api/v1/dashboard/admin/summary")
def summary(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    return {"cleanrooms": db.scalar(select(func.count(Cleanroom.id))), "active_people": db.scalar(select(func.count(User.id)).where(User.status == "ACTIVE")), "devices": db.scalar(select(func.count(Device.id))), "readings": db.scalar(select(func.count(SensorReading.id))), "active_alerts": db.scalar(select(func.count(Alert.id)).where(Alert.status == "ACTIVE"))}
