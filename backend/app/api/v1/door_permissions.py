from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin_key
from app.core.rfid import normalize_rfid_uid
from app.db.database import get_db
from app.models import DoorPermission, RfidTag
from app.schemas.door_access import (
    DoorPermissionCreate,
    DoorPermissionRead,
    DoorPermissionUpdate,
)


router = APIRouter(
    prefix="/permissions",
    tags=["Access permissions"],
    dependencies=[Depends(require_admin_key)],
)


@router.post("/", response_model=DoorPermissionRead, status_code=status.HTTP_201_CREATED)
def create_permission(
    permission_data: DoorPermissionCreate,
    session: Session = Depends(get_db),
):
    uid = normalize_rfid_uid(permission_data.rfid_uid)
    if not uid:
        raise HTTPException(status_code=422, detail="UID RFID inválido")
    if session.get(RfidTag, uid) is None:
        raise HTTPException(status_code=404, detail="Tag RFID no encontrado")
    existing = session.scalar(
        select(DoorPermission).where(
            DoorPermission.rfid_uid == uid,
            DoorPermission.door_id == permission_data.door_id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un permiso para esta tarjeta y puerta",
        )
    permission = DoorPermission(
        rfid_uid=uid,
        door_id=permission_data.door_id,
        is_active=permission_data.is_active,
    )
    session.add(permission)
    session.commit()
    session.refresh(permission)
    return permission


@router.get("/", response_model=list[DoorPermissionRead])
def get_permissions(door_id: str | None = None, session: Session = Depends(get_db)):
    statement = select(DoorPermission)
    if door_id is not None:
        statement = statement.where(DoorPermission.door_id == door_id)
    return session.scalars(statement.order_by(desc(DoorPermission.id))).all()


@router.patch("/{permission_id}", response_model=DoorPermissionRead)
def update_permission(
    permission_id: int,
    permission_data: DoorPermissionUpdate,
    session: Session = Depends(get_db),
):
    permission = session.get(DoorPermission, permission_id)
    if permission is None:
        raise HTTPException(status_code=404, detail="Permiso no encontrado")
    if permission_data.is_active is not None:
        permission.is_active = permission_data.is_active
    session.commit()
    session.refresh(permission)
    return permission


@router.delete("/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_permission(permission_id: int, session: Session = Depends(get_db)):
    permission = session.get(DoorPermission, permission_id)
    if permission is None:
        raise HTTPException(status_code=404, detail="Permiso no encontrado")
    session.delete(permission)
    session.commit()