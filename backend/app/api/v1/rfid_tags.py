from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rfid import normalize_rfid_uid
from app.db.database import get_db
from app.models.employee import Employee
from app.models.rfid_tag import RfidTag
from app.schemas.rfid_tag import RfidTagCreate, RfidTagRead, RfidTagUpdate


router = APIRouter(prefix="/rfid-tags", tags=["RFID tags"])


@router.post("/", response_model=RfidTagRead, status_code=status.HTTP_201_CREATED)
def create_rfid_tag(tag_data: RfidTagCreate, session: Session = Depends(get_db)):
    uid = normalize_rfid_uid(tag_data.uid)
    if session.get(RfidTag, uid) is not None:
        raise HTTPException(status_code=409, detail="El tag RFID ya existe")
    if tag_data.employee_id is not None and session.get(Employee, tag_data.employee_id) is None:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    tag_values = tag_data.model_dump(exclude={"uid"})
    tag = RfidTag(uid=uid, **tag_values)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


@router.get("/", response_model=list[RfidTagRead])
def get_rfid_tags(session: Session = Depends(get_db)):
    return session.scalars(select(RfidTag)).all()


@router.get("/{uid}", response_model=RfidTagRead)
def get_rfid_tag(uid: str, session: Session = Depends(get_db)):
    tag = session.get(RfidTag, normalize_rfid_uid(uid))
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag RFID no encontrado")
    return tag


@router.put("/{uid}", response_model=RfidTagRead)
def update_rfid_tag(
    uid: str,
    tag_data: RfidTagUpdate,
    session: Session = Depends(get_db),
):
    tag = session.get(RfidTag, normalize_rfid_uid(uid))
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag RFID no encontrado")
    update_data = tag_data.model_dump(exclude_unset=True)
    employee_id = update_data.get("employee_id")
    if employee_id is not None and session.get(Employee, employee_id) is None:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    for field, value in update_data.items():
        setattr(tag, field, value)
    session.commit()
    session.refresh(tag)
    return tag


@router.delete("/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rfid_tag(uid: str, session: Session = Depends(get_db)):
    tag = session.get(RfidTag, normalize_rfid_uid(uid))
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag RFID no encontrado")
    session.delete(tag)
    session.commit()