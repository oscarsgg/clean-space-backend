from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import output, require
from app.core.security import hash_rfid
from app.db.database import get_db
from app.models import AccessPermission, Cleanroom, RfidCard, User
from app.schemas.access import PermissionIn, PermissionUpdate
from app.schemas.devices import CardIn, CardUpdate


router = APIRouter(tags=["Access"])


@router.get("/tarjetas")
def cards(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    return output(db.scalars(select(RfidCard)).all())


@router.post("/personas/{id}/tarjetas", status_code=201)
def add_card(
    id: int,
    data: CardIn,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    if id != data.user_id or not db.get(User, id):
        raise HTTPException(400, "Persona inválida")
    item = RfidCard(
        user_id=id,
        uid_hash=hash_rfid(data.uid),
        label=data.label,
        expires_at=data.expires_at,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return output(item)


@router.patch("/tarjetas/{id}")
def update_card(
    id: int,
    data: CardUpdate,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    item = db.get(RfidCard, id)
    if not item:
        raise HTTPException(404, "Tarjeta no encontrada")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    return output(item)


@router.get("/permisos")
def permissions(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    return output(db.scalars(select(AccessPermission)).all())


@router.post("/permisos", status_code=201)
def create_permission(
    data: PermissionIn,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    if not db.get(User, data.user_id) or not db.get(Cleanroom, data.cleanroom_id):
        raise HTTPException(400, "Persona o cuarto inválido")
    item = AccessPermission(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return output(item)


@router.patch("/permisos/{id}")
def update_permission(
    id: int,
    data: PermissionUpdate,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    item = db.get(AccessPermission, id)
    if not item:
        raise HTTPException(404, "Permiso no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    return output(item)