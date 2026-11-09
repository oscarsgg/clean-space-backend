import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import current_user, ensure_room_access, output, require
from app.core.security import hash_refresh
from app.db.database import get_db
from app.models import Cleanroom, Device
from app.schemas.devices import DeviceIn, DeviceUpdate


router = APIRouter(prefix="/dispositivos", tags=["Devices"])


@router.get("")
def devices(db: Session = Depends(get_db), user=Depends(current_user)):
    items = db.scalars(select(Device)).all()
    if "ADMINISTRADOR" not in {role.code for role in user.roles}:
        room_ids = {room.id for room in user.assigned_cleanrooms}
        items = [device for device in items if device.cleanroom_id in room_ids]
    return output(items)


@router.get("/{id}")
def get_device(id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    item = db.get(Device, id)
    if not item:
        raise HTTPException(404, "Dispositivo no encontrado")
    ensure_room_access(user, item.cleanroom_id)
    return output(item)


@router.post("", status_code=201)
def create_device(
    data: DeviceIn,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    if not db.get(Cleanroom, data.cleanroom_id):
        raise HTTPException(400, "Cuarto inválido")
    item = Device(**data.model_dump())
    api_key = secrets.token_urlsafe(32)
    item.api_key_hash = hash_refresh(api_key)
    db.add(item)
    db.commit()
    db.refresh(item)
    return {**output(item), "api_key": api_key}


@router.put("/{id}")
@router.patch("/{id}")
def update_device(
    id: int,
    data: DeviceUpdate,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    item = db.get(Device, id)
    if not item:
        raise HTTPException(404, "Dispositivo no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return output(item)


@router.delete("/{id}", status_code=204)
def delete_device(
    id: int,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    item = db.get(Device, id)
    if not item:
        raise HTTPException(404, "Dispositivo no encontrado")
    item.status = "INACTIVE"
    db.commit()


@router.post("/{id}/llave")
def regenerate_device_key(
    id: int,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    item = db.get(Device, id)
    if not item:
        raise HTTPException(404, "Dispositivo no encontrado")
    api_key = secrets.token_urlsafe(32)
    item.api_key_hash = hash_refresh(api_key)
    db.commit()
    return {"device_id": id, "api_key": api_key}