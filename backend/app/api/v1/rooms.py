from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import current_user, ensure_room_access, output, require
from app.db.database import get_db
from app.models import Cleanroom, MonitoringParameter
from app.schemas.monitoring import ParameterIn
from app.schemas.rooms import RoomIn, RoomUpdate


router = APIRouter(prefix="/cuartos", tags=["Rooms"])


@router.get("")
def rooms(db: Session = Depends(get_db), user=Depends(current_user)):
    items = db.scalars(select(Cleanroom)).all()
    if "ADMINISTRADOR" not in {role.code for role in user.roles}:
        items = [room for room in items if room in user.assigned_cleanrooms]
    return output(items)


@router.post("", status_code=201)
def create_room(
    data: RoomIn,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    if db.scalar(select(Cleanroom).where(Cleanroom.code == data.code)):
        raise HTTPException(409, "Código duplicado")
    item = Cleanroom(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return output(item)


@router.put("/{id}")
@router.patch("/{id}")
def update_room(
    id: int,
    data: RoomUpdate,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    item = db.get(Cleanroom, id)
    if not item:
        raise HTTPException(404, "Cuarto no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return output(item)


@router.delete("/{id}", status_code=204)
def delete_room(
    id: int,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    item = db.get(Cleanroom, id)
    if not item:
        raise HTTPException(404, "Cuarto no encontrado")
    item.status = "INACTIVE"
    db.commit()


@router.get("/{id}/parametros")
def room_parameters(id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    ensure_room_access(user, id)
    return output(
        db.scalars(select(MonitoringParameter).where(MonitoringParameter.cleanroom_id == id)).all()
    )


@router.put("/{id}/parametros")
def set_room_parameters(
    id: int,
    data: list[ParameterIn],
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR")),
):
    if not db.get(Cleanroom, id):
        raise HTTPException(404, "Cuarto no encontrado")
    for param in data:
        item = db.scalar(
            select(MonitoringParameter).where(
                MonitoringParameter.cleanroom_id == id,
                MonitoringParameter.metric == param.metric,
            )
        )
        if not item:
            item = MonitoringParameter(cleanroom_id=id, metric=param.metric)
            db.add(item)
        for key, value in param.model_dump().items():
            setattr(item, key, value)
    db.commit()
    return output(
        db.scalars(select(MonitoringParameter).where(MonitoringParameter.cleanroom_id == id)).all()
    )