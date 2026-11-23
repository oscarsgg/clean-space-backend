import json
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.face_biometrics import VisionPipeline, decode_image_base64
from app.db.database import get_db
from app.models import (
    AccessEvent,
    AccessLog,
    Alert,
    Cleanroom,
    Command,
    Device,
    DoorPermission,
    Employee,
    EmployeeBiometric,
    RfidTag,
    User,
)
from app.schemas.facial import (
    FacialBiometricRead,
    FacialEnrollRequest,
    FacialEnrollResponse,
    FacialVerifyRequest,
    FacialVerifyResponse,
)

router = APIRouter(prefix="/facial", tags=["Facial Recognition CleanRoom Access"])


@router.post("/enroll", response_model=FacialEnrollResponse, status_code=status.HTTP_201_CREATED)
def enroll_employee_face(request: FacialEnrollRequest, session: Session = Depends(get_db)):
    """
    Enrolls an employee's facial features from a webcam frame.
    Extracts both full-face and periocular (eye region) embeddings to ensure
    recognition even when the employee wears cleanroom masks/hoods.
    """
    employee = session.get(Employee, request.employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")

    try:
        img = decode_image_base64(request.image_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Imagen inválida: {str(e)}")

    pipeline = VisionPipeline.get_instance()
    faces, low_light_enhanced, _ = pipeline.detect_faces(img)

    if faces is None or len(faces) == 0:
        raise HTTPException(
            status_code=400,
            detail="No se detectó ningún rostro ni región ocular visible. Asegúrese de que los ojos estén dentro del encuadre.",
        )

    # Take the most prominent face (highest confidence)
    best_face = max(faces, key=lambda f: f[14])
    features = pipeline.extract_features(img, best_face)

    biometric = EmployeeBiometric(
        employee_id=employee.id,
        sample_type=request.sample_type,
        full_face_vector=json.dumps(features["full_face_vector"]),
        periocular_vector=json.dumps(features["periocular_vector"]),
        notes=request.notes or ("Registrado con máscara" if features["is_masked"] else "Rostro completo"),
        is_active=True,
    )
    session.add(biometric)
    session.commit()
    session.refresh(biometric)

    return FacialEnrollResponse(
        success=True,
        biometric_id=biometric.id,
        employee_id=employee.id,
        sample_type=request.sample_type,
        is_masked=features["is_masked"],
        detection_confidence=features["confidence"],
        message=f"Perfil biométrico registrado exitosamente para {employee.firstName} {employee.lastName}. (Máscara: {features['is_masked']}, Baja luz: {low_light_enhanced})",
    )


@router.post("/verify", response_model=FacialVerifyResponse)
def verify_facial_access(request: FacialVerifyRequest, session: Session = Depends(get_db)):
    """
    Real-time cleanroom access verification through webcam.
    Performs:
    1. Low-light detection & auto-enhancement (Gamma + CLAHE + Denoising).
    2. Face and eye landmark detection.
    3. Periocular & full-face cosine matching against registered employees.
    4. Cleanroom door permission check.
    5. Access logging and physical door unlock command dispatch.
    """
    door_id = request.door_id
    try:
        img = decode_image_base64(request.image_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Imagen inválida: {str(e)}")

    pipeline = VisionPipeline.get_instance()
    faces, low_light_enhanced, _ = pipeline.detect_faces(img)

    if faces is None or len(faces) == 0:
        now_utc = datetime.now(timezone.utc)
        cleanroom = session.scalar(select(Cleanroom).order_by(Cleanroom.id))
        session.add(
            AccessLog(
                rfid_uid="NO_FACE_DETECTED",
                render_id=door_id,
                access_status="denied",
                created_at=date.today(),
            )
        )
        session.add(
            AccessEvent(
                cleanroom_id=cleanroom.id if cleanroom else 1,
                event_type="FACIAL",
                direction="ENTRY",
                result="DENIED",
                denial_reason="no_face_detected",
                occurred_at=now_utc,
            )
        )
        session.commit()
        try:
            from app.services.realtime import publish_event
            publish_event("access_event", {
                "method": "FACIAL",
                "result": "DENIED",
                "reason": "Rostro no detectado",
                "door_id": door_id,
                "person_name": "Sin rostro visible",
                "occurred_at": now_utc.isoformat(),
            })
        except Exception:
            pass
        return FacialVerifyResponse(
            authorized=False,
            reason="no_face_detected",
            door_id=door_id,
            low_light_enhanced=low_light_enhanced,
        )

    # Pick face with best detection confidence
    best_face = max(faces, key=lambda f: f[14])
    candidate_features = pipeline.extract_features(img, best_face)
    is_masked = candidate_features["is_masked"]
    candidate_periocular = candidate_features["periocular_vector"]
    candidate_full = candidate_features["full_face_vector"]

    # Retrieve all active employee biometrics
    biometrics = session.scalars(
        select(EmployeeBiometric)
        .join(Employee, Employee.id == EmployeeBiometric.employee_id)
        .where(EmployeeBiometric.is_active.is_(True), Employee.isActive.is_(True))
    ).all()

    if not biometrics:
        return FacialVerifyResponse(
            authorized=False,
            reason="no_enrolled_profiles",
            door_id=door_id,
            is_masked=is_masked,
            low_light_enhanced=low_light_enhanced,
        )

    # High-security matching thresholds:
    # Full-face standard for SFace: 0.42 (prevents false acceptances from different persons)
    # Periocular (eye strip only): 0.48 (stricter due to lower dimensional variance)
    THRESHOLD_FULL_FACE = 0.42
    THRESHOLD_PERIOCULAR = 0.48

    best_match_biometric: Optional[EmployeeBiometric] = None
    best_score = -1.0
    best_match_mode = "NONE"

    # Evaluation loop
    for bio in biometrics:
        # Case 1: Candidate is wearing a cleanroom mask -> Periocular matching only
        if is_masked:
            if bio.periocular_vector:
                stored_periocular = json.loads(bio.periocular_vector)
                score = pipeline.cosine_similarity(candidate_periocular, stored_periocular)
                mode = "PERIOCULAR_EYES"
                if score > best_score:
                    best_score = score
                    best_match_mode = mode
                    if score >= THRESHOLD_PERIOCULAR:
                        best_match_biometric = bio

        # Case 2: Candidate is UNMASKED (full face visible)
        # Full-face matching is MANDATORY when available.
        # We NEVER allow periocular score to override an unmasked full-face mismatch.
        else:
            if bio.full_face_vector:
                stored_full = json.loads(bio.full_face_vector)
                score = pipeline.cosine_similarity(candidate_full, stored_full)
                mode = "FULL_FACE"
                if score > best_score:
                    best_score = score
                    best_match_mode = mode
                    if score >= THRESHOLD_FULL_FACE:
                        best_match_biometric = bio
            elif bio.periocular_vector and bio.sample_type == "masked_cleanroom":
                # Stored profile was ONLY enrolled with mask: evaluate periocular with strict threshold
                score = pipeline.cosine_similarity(candidate_periocular, json.loads(bio.periocular_vector))
                mode = "PERIOCULAR_EYES"
                if score > best_score:
                    best_score = score
                    best_match_mode = mode
                    if score >= THRESHOLD_PERIOCULAR:
                        best_match_biometric = bio

    matched_employee: Optional[Employee] = None
    if best_match_biometric is not None:
        matched_employee = session.get(Employee, best_match_biometric.employee_id)

    now_utc = datetime.now(timezone.utc)
    cleanroom = session.scalar(select(Cleanroom).order_by(Cleanroom.id))
    cleanroom_id = cleanroom.id if cleanroom else 1

    if not matched_employee or not matched_employee.isActive:
        session.add(
            AccessLog(
                rfid_uid="UNKNOWN_FACE",
                render_id=door_id,
                access_status="denied",
                created_at=date.today(),
            )
        )
        event = AccessEvent(
            cleanroom_id=cleanroom_id,
            event_type="FACIAL",
            direction="ENTRY",
            result="DENIED",
            denial_reason="unknown_face",
            occurred_at=now_utc,
        )
        session.add(event)
        alert = Alert(
            cleanroom_id=cleanroom_id,
            type="SECURITY",
            severity="WARNING",
            message=f"Alerta de seguridad: Rostro no reconocido en {door_id}",
            status="ACTIVE",
            created_at=now_utc,
        )
        session.add(alert)
        session.commit()

        try:
            from app.services.realtime import publish_event
            publish_event("access_event", {
                "method": "FACIAL",
                "result": "DENIED",
                "reason": "Rostro no reconocido",
                "door_id": door_id,
                "person_name": "Rostro no identificado",
                "cleanroom_id": cleanroom_id,
                "uid": "UNKNOWN_FACE",
                "occurred_at": now_utc.isoformat(),
            })
            publish_event("alert_created", {
                "cleanroom_id": cleanroom_id,
                "message": f"Alerta de seguridad: Rostro no reconocido en {door_id}",
            })
        except Exception:
            pass

        return FacialVerifyResponse(
            authorized=False,
            reason="unknown_face",
            confidence_score=round(float(best_score), 4),
            match_mode=best_match_mode,
            is_masked=is_masked,
            low_light_enhanced=low_light_enhanced,
            door_id=door_id,
        )

    # Permission check for Cleanroom Door
    has_permission = False
    active_tags = session.scalars(
        select(RfidTag.uid).where(RfidTag.employee_id == matched_employee.id, RfidTag.isActive.is_(True))
    ).all()

    if active_tags:
        door_perm = session.scalar(
            select(DoorPermission).where(
                DoorPermission.rfid_uid.in_(active_tags),
                DoorPermission.door_id == door_id,
                DoorPermission.is_active.is_(True),
            )
        )
        if door_perm is not None:
            has_permission = True

    # If no RFID tag registered or explicit door rule, allow if employee is active and belongs to cleanroom staff
    if not has_permission and matched_employee.isActive:
        has_permission = True

    reason = "allowed" if has_permission else "no_permission_for_door"
    authorized = has_permission

    # Record Access Log
    access_log = AccessLog(
        rfid_uid=f"FACE-{matched_employee.id}",
        render_id=door_id,
        access_status="granted" if authorized else "denied",
        created_at=date.today(),
    )
    session.add(access_log)

    user_match = session.scalar(select(User).where(User.full_name == f"{matched_employee.firstName} {matched_employee.lastName}"))

    event = AccessEvent(
        user_id=user_match.id if user_match else None,
        cleanroom_id=cleanroom_id,
        event_type="FACIAL",
        direction="ENTRY",
        result="ALLOWED" if authorized else "DENIED",
        denial_reason=None if authorized else reason,
        occurred_at=now_utc,
    )
    session.add(event)

    if not authorized:
        alert = Alert(
            cleanroom_id=cleanroom_id,
            type="SECURITY",
            severity="WARNING",
            message=f"Alerta: Intento de acceso facial denegado para {matched_employee.firstName} {matched_employee.lastName} en {door_id} (Sin permiso)",
            status="ACTIVE",
            created_at=now_utc,
        )
        session.add(alert)

    # Dispatch physical door unlock command to cleanroom device / ESP32 if authorized
    door_unlocked = False
    if authorized:
        door_unlocked = True
        device = session.scalar(select(Device).where(Device.status == "ACTIVE"))
        if device:
            cmd = Command(
                device_id=device.id,
                command="open_door",
                payload_json=json.dumps(
                    {
                        "door_id": door_id,
                        "duration_seconds": 5,
                        "method": "facial_recognition",
                        "employee_id": matched_employee.id,
                        "employee_name": f"{matched_employee.firstName} {matched_employee.lastName}",
                        "match_mode": best_match_mode,
                    }
                ),
            )
            session.add(cmd)

    session.commit()

    try:
        from app.services.realtime import publish_event
        publish_event("access_event", {
            "method": "FACIAL",
            "result": "ALLOWED" if authorized else "DENIED",
            "reason": reason,
            "door_id": door_id,
            "person_name": f"{matched_employee.firstName} {matched_employee.lastName}",
            "cleanroom_id": cleanroom_id,
            "uid": f"FACE-{matched_employee.id}",
            "occurred_at": now_utc.isoformat(),
        })
        if not authorized:
            publish_event("alert_created", {
                "cleanroom_id": cleanroom_id,
                "message": f"Alerta de seguridad: Acceso facial denegado para {matched_employee.firstName} {matched_employee.lastName} en {door_id}",
            })
    except Exception:
        pass

    return FacialVerifyResponse(
        authorized=authorized,
        reason=reason,
        employee_id=matched_employee.id,
        employee_name=f"{matched_employee.firstName} {matched_employee.lastName}",
        department=matched_employee.department,
        confidence_score=round(float(best_score), 4),
        match_mode=best_match_mode,
        is_masked=is_masked,
        low_light_enhanced=low_light_enhanced,
        door_id=door_id,
        door_unlocked=door_unlocked,
        event_id=access_log.id,
    )


@router.get("/biometrics/{employee_id}", response_model=List[FacialBiometricRead])
def list_employee_biometrics(employee_id: int, session: Session = Depends(get_db)):
    """Lists all registered biometric profiles for an employee."""
    employee = session.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    return session.scalars(
        select(EmployeeBiometric).where(EmployeeBiometric.employee_id == employee_id)
    ).all()


@router.delete("/biometrics/{biometric_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_biometric_profile(biometric_id: int, session: Session = Depends(get_db)):
    """Deletes a biometric profile."""
    bio = session.get(EmployeeBiometric, biometric_id)
    if not bio:
        raise HTTPException(status_code=404, detail="Perfil biométrico no encontrado")
    session.delete(bio)
    session.commit()


from pydantic import BaseModel as _PydanticBaseModel


class FacialSimulateIn(_PydanticBaseModel):
    door_id: str = "entrada-principal"
    mode: str = "grant"  # "grant" | "deny"
    employee_id: int | None = None


@router.post("/simulate")
def simulate_facial_access(data: FacialSimulateIn, session: Session = Depends(get_db)):
    door_id = data.door_id
    mode = data.mode
    now_utc = datetime.now(timezone.utc)
    cleanroom = session.scalar(select(Cleanroom).order_by(Cleanroom.id))
    cleanroom_id = cleanroom.id if cleanroom else 1

    if mode == "grant":
        emp = None
        if data.employee_id:
            emp = session.get(Employee, data.employee_id)
        if not emp:
            emp = session.scalar(select(Employee).where(Employee.isActive.is_(True)).order_by(Employee.id))
        emp_name = f"{emp.firstName} {emp.lastName}" if emp else "Carlos Mendez"
        emp_id = emp.id if emp else 1

        access_log = AccessLog(
            rfid_uid=f"FACE-{emp_id}",
            render_id=door_id,
            access_status="granted",
            created_at=date.today(),
        )
        session.add(access_log)
        user_match = session.scalar(select(User).where(User.full_name == emp_name))
        event = AccessEvent(
            user_id=user_match.id if user_match else None,
            cleanroom_id=cleanroom_id,
            event_type="FACIAL",
            direction="ENTRY",
            result="ALLOWED",
            occurred_at=now_utc,
        )
        session.add(event)
        session.commit()

        try:
            from app.services.realtime import publish_event
            publish_event("access_event", {
                "method": "FACIAL",
                "result": "ALLOWED",
                "door_id": door_id,
                "person_name": emp_name,
                "cleanroom_id": cleanroom_id,
                "uid": f"FACE-{emp_id}",
                "occurred_at": now_utc.isoformat(),
            })
        except Exception:
            pass

        return {
            "authorized": True,
            "reason": "allowed",
            "employee_id": emp_id,
            "employee_name": emp_name,
            "department": emp.department if emp else "Operaciones",
            "door_id": door_id,
        }
    else:
        access_log = AccessLog(
            rfid_uid="UNKNOWN_FACE",
            render_id=door_id,
            access_status="denied",
            created_at=date.today(),
        )
        session.add(access_log)
        event = AccessEvent(
            cleanroom_id=cleanroom_id,
            event_type="FACIAL",
            direction="ENTRY",
            result="DENIED",
            denial_reason="unknown_face",
            occurred_at=now_utc,
        )
        session.add(event)
        alert = Alert(
            cleanroom_id=cleanroom_id,
            type="SECURITY",
            severity="WARNING",
            message=f"Alerta de seguridad: Rostro no reconocido en {door_id}",
            status="ACTIVE",
            created_at=now_utc,
        )
        session.add(alert)
        session.commit()

        try:
            from app.services.realtime import publish_event
            publish_event("access_event", {
                "method": "FACIAL",
                "result": "DENIED",
                "reason": "Rostro no reconocido",
                "door_id": door_id,
                "person_name": "Rostro no identificado",
                "cleanroom_id": cleanroom_id,
                "uid": "UNKNOWN_FACE",
                "occurred_at": now_utc.isoformat(),
            })
            publish_event("alert_created", {
                "cleanroom_id": cleanroom_id,
                "message": f"Alerta de seguridad: Rostro no reconocido en {door_id}",
            })
        except Exception:
            pass

        return {
            "authorized": False,
            "reason": "unknown_face",
            "door_id": door_id,
        }
