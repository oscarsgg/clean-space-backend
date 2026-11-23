from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import require_device_key
from app.core.rfid import normalize_rfid_uid
from app.db.database import get_db
from app.models import AccessEvent, AccessLog, Cleanroom, DoorPermission, Employee, RfidTag, User
from app.schemas.door_access import (
    DeviceAccessRequest,
    DeviceAccessResponse,
    DeviceSyncResponse,
    OfflineEventsRequest,
)

_last_scanned_card = {
    "uid": None,
    "door_id": None,
    "scanned_at": None,
    "authorized": None,
    "reason": None,
    "person_name": None,
}


def get_last_scanned_card():
    return _last_scanned_card


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
    global _last_scanned_card
    uid = normalize_rfid_uid(access_request.uid)
    door_id = access_request.door_id
    raw_uid = str(access_request.uid).strip()
    tag = session.get(RfidTag, uid) or session.get(RfidTag, raw_uid)
    reason = "unknown_tag"
    employee_name = None

    if tag is not None:
        reason = "inactive_tag"
        if tag.isActive:
            reason = "unassigned_tag"
            if tag.employee_id is not None:
                employee = session.get(Employee, tag.employee_id)
                reason = "inactive_employee"
                if employee is not None and employee.isActive:
                    employee_name = f"{employee.firstName} {employee.lastName}"
                    permission = session.scalar(
                        select(DoorPermission).where(
                            (DoorPermission.rfid_uid == uid) | (DoorPermission.rfid_uid == raw_uid) | (DoorPermission.rfid_uid == tag.uid),
                            DoorPermission.door_id == door_id,
                            DoorPermission.is_active.is_(True),
                        )
                    )
                    reason = "no_permission_for_door"
                    if permission is not None:
                        reason = "allowed"

    authorized = reason == "allowed"
    now_utc = datetime.now(timezone.utc)

    _last_scanned_card = {
        "uid": uid,
        "door_id": door_id,
        "scanned_at": now_utc.isoformat(),
        "authorized": authorized,
        "reason": reason,
        "person_name": employee_name,
    }

    session.add(
        AccessLog(
            rfid_uid=uid,
            render_id=door_id,
            access_status="granted" if authorized else "denied",
            created_at=date.today(),
        )
    )

    cleanroom = session.scalar(select(Cleanroom).order_by(Cleanroom.id))
    cleanroom_id = cleanroom.id if cleanroom else 1
    user_match = None
    if employee_name:
        user_match = session.scalar(select(User).where(User.full_name == employee_name))

    event = AccessEvent(
        user_id=user_match.id if user_match else None,
        cleanroom_id=cleanroom_id,
        event_type="RFID_IOT",
        direction="ENTRY",
        result="ALLOWED" if authorized else "DENIED",
        denial_reason=None if authorized else reason,
        occurred_at=now_utc,
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    try:
        from app.services.realtime import publish_event

        publish_event(
            "access_event",
            {
                "event_id": event.id,
                "cleanroom_id": cleanroom_id,
                "door_id": door_id,
                "uid": uid,
                "result": "ALLOWED" if authorized else "DENIED",
                "direction": "ENTRY",
                "reason": reason,
                "person_name": employee_name or f"Tarjeta {uid}",
                "occurred_at": now_utc.isoformat(),
            },
        )
        publish_event(
            "card_scanned",
            {
                "uid": uid,
                "door_id": door_id,
                "authorized": authorized,
                "reason": reason,
                "person_name": employee_name,
                "occurred_at": now_utc.isoformat(),
            },
        )
    except Exception:
        pass

    return DeviceAccessResponse(authorized=authorized, reason=reason, uid=uid, door_id=door_id)


@router.post("/events", status_code=202)
def upload_offline_events(events_request: OfflineEventsRequest, session: Session = Depends(get_db)):
    cleanroom = session.scalar(select(Cleanroom).order_by(Cleanroom.id))
    cleanroom_id = cleanroom.id if cleanroom else 1
    now_utc = datetime.now(timezone.utc)

    for event in events_request.events:
        norm_uid = normalize_rfid_uid(event.uid)
        session.add(
            AccessLog(
                rfid_uid=norm_uid,
                render_id=event.door_id,
                access_status=event.access_status,
                created_at=event.created_at,
            )
        )
        is_allowed = "allowed" in str(event.access_status).lower()
        session.add(
            AccessEvent(
                cleanroom_id=cleanroom_id,
                event_type="RFID_OFFLINE",
                direction="ENTRY",
                result="ALLOWED" if is_allowed else "DENIED",
                denial_reason=None if is_allowed else "offline_denied",
                occurred_at=now_utc,
            )
        )
    session.commit()
    try:
        from app.services.realtime import publish_event

        publish_event("access_event", {"batch_count": len(events_request.events)})
    except Exception:
        pass
    return {"accepted": len(events_request.events)}
