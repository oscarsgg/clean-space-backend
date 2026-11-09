from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import require_device_key
from app.core.rfid import normalize_rfid_uid
from app.db.database import get_db
from app.models import AccessLog, DoorPermission, Employee, RfidTag
from app.schemas.door_access import (
    DeviceAccessRequest,
    DeviceAccessResponse,
    DeviceSyncResponse,
    OfflineEventsRequest,
)


router = APIRouter(
    prefix="/device",
    tags=["Access device"],
    dependencies=[Depends(require_device_key)],
)


@router.get("/sync", response_model=DeviceSyncResponse)
def sync_permissions(door_id: str, session: Session = Depends(get_db)):
    statement = (
        select(DoorPermission.rfid_uid)
        .join(RfidTag, RfidTag.uid == DoorPermission.rfid_uid)
        .join(Employee, Employee.id == RfidTag.employee_id)
        .where(
            DoorPermission.door_id == door_id,
            DoorPermission.is_active.is_(True),
            RfidTag.isActive.is_(True),
            Employee.isActive.is_(True),
        )
    )
    return DeviceSyncResponse(door_id=door_id, allowed_uids=session.scalars(statement).all())


@router.post("/access", response_model=DeviceAccessResponse)
def check_access(access_request: DeviceAccessRequest, session: Session = Depends(get_db)):
    uid = normalize_rfid_uid(access_request.uid)
    door_id = access_request.door_id
    tag = session.get(RfidTag, uid)
    reason = "unknown_tag"

    if tag is not None:
        reason = "inactive_tag"
        if tag.isActive:
            reason = "unassigned_tag"
            if tag.employee_id is not None:
                employee = session.get(Employee, tag.employee_id)
                reason = "inactive_employee"
                if employee is not None and employee.isActive:
                    permission = session.scalar(
                        select(DoorPermission).where(
                            DoorPermission.rfid_uid == uid,
                            DoorPermission.door_id == door_id,
                            DoorPermission.is_active.is_(True),
                        )
                    )
                    reason = "no_permission_for_door"
                    if permission is not None:
                        reason = "allowed"

    authorized = reason == "allowed"
    session.add(
        AccessLog(
            rfid_uid=uid,
            render_id=door_id,
            access_status="granted" if authorized else "denied",
            created_at=date.today(),
        )
    )
    session.commit()
    return DeviceAccessResponse(authorized=authorized, reason=reason, uid=uid, door_id=door_id)


@router.post("/events", status_code=202)
def upload_offline_events(events_request: OfflineEventsRequest, session: Session = Depends(get_db)):
    for event in events_request.events:
        session.add(
            AccessLog(
                rfid_uid=normalize_rfid_uid(event.uid),
                render_id=event.door_id,
                access_status=event.access_status,
                created_at=event.created_at,
            )
        )
    session.commit()
    return {"accepted": len(events_request.events)}