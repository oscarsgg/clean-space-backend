from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import hash_password, hash_rfid
from app.db.database import Base, SessionLocal, engine, ensure_schema_updates
from app.models import (
    AccessEvent,
    AccessPermission,
    AgentDecisionLog,
    Cleanroom,
    Device,
    MonitoringParameter,
    RfidCard,
    Role,
    SensorReading,
    User,
)


def run_seed():
    Base.metadata.create_all(bind=engine)
    ensure_schema_updates(engine)

    db = SessionLocal()
    settings = get_settings()
    try:
        roles_meta = {
            "ADMINISTRADOR": "Administrador",
            "SUPERVISOR": "Supervisor",
            "PERSONAL_AUTORIZADO": "Personal autorizado",
        }
        for code, name in roles_meta.items():
            if not db.scalar(select(Role).where(Role.code == code)):
                db.add(Role(code=code, name=name))
        db.commit()

        def ensure_user(email: str, password: str, name: str, role_code: str) -> User:
            existing = db.scalar(select(User).where(User.email == email))
            if not existing:
                role = db.scalar(select(Role).where(Role.code == role_code))
                existing = User(
                    email=email,
                    password_hash=hash_password(password),
                    full_name=name,
                )
                existing.roles = [role] if role else []
                db.add(existing)
                db.commit()
                db.refresh(existing)
            return existing

        ensure_user(
            settings.initial_admin_email,
            settings.initial_admin_password,
            "CleanSpace Administrator",
            "ADMINISTRADOR",
        )
        sup = ensure_user(
            "supervisor@cleanspace.local",
            "Supervisor_123!",
            "Supervisión CleanSpace",
            "SUPERVISOR",
        )
        p1 = ensure_user(
            "personal@cleanspace.local",
            "Personal_123!",
            "Personal autorizado",
            "PERSONAL_AUTORIZADO",
        )
        p2 = ensure_user(
            "elena@cleanspace.local",
            "Personal_123!",
            "Dra. Elena Vázquez",
            "PERSONAL_AUTORIZADO",
        )

        room_specs = [
            ("SALA-A", "Laboratorio principal", 6, "ISO 5"),
            ("SALA-B", "Zona de preparación", 8, "ISO 7"),
        ]
        for code, name, capacity, iso_class in room_specs:
            room = db.scalar(select(Cleanroom).where(Cleanroom.code == code))
            if not room:
                room = Cleanroom(
                    code=code,
                    name=name,
                    location="Edificio académico",
                    capacity=capacity,
                    iso_class=iso_class,
                )
                db.add(room)
            else:
                room.capacity = capacity
                room.iso_class = iso_class
        db.commit()

        rooms = list(db.scalars(select(Cleanroom).order_by(Cleanroom.id)).all())
        sup.assigned_cleanrooms = rooms
        db.commit()

        param_specs = [
            ("temperature", 18.0, 24.0, "°C"),
            ("humidity", 35.0, 55.0, "%"),
            ("pm25", 0.0, 200.0, "p/m³"),
            ("pressure", 10.0, 15.0, "Pa"),
        ]
        devices_by_room: dict[int, Device] = {}
        for idx, room in enumerate(rooms, 1):
            dev = db.scalar(select(Device).where(Device.code == f"DEV-{idx}"))
            if not dev:
                dev = Device(
                    cleanroom_id=room.id,
                    code=f"DEV-{idx}",
                    type="SENSOR",
                    serial_number=f"SIM-{idx}",
                )
                db.add(dev)
                db.flush()
            devices_by_room[room.id] = dev

            for metric, min_v, max_v, unit in param_specs:
                param = db.scalar(
                    select(MonitoringParameter).where(
                        MonitoringParameter.cleanroom_id == room.id,
                        MonitoringParameter.metric == metric,
                    )
                )
                if not param:
                    db.add(
                        MonitoringParameter(
                            cleanroom_id=room.id,
                            metric=metric,
                            min_value=min_v,
                            max_value=max_v,
                            unit=unit,
                            enabled=True,
                        )
                    )
        db.commit()

        cards_by_user: dict[int, RfidCard] = {}
        for target_user, raw_uid in [
            (p1, "04-A1-B2-C3"),
            (p2, "04-D4-E5-F6"),
            (sup, "04-11-22-33"),
        ]:
            uid_h = hash_rfid(raw_uid)
            card = db.scalar(select(RfidCard).where(RfidCard.uid_hash == uid_h))
            if not card:
                card = RfidCard(user_id=target_user.id, uid_hash=uid_h, label=raw_uid)
                db.add(card)
                db.flush()
            elif not card.label:
                card.label = raw_uid
            cards_by_user[target_user.id] = card

            for room in rooms:
                perm = db.scalar(
                    select(AccessPermission).where(
                        AccessPermission.user_id == target_user.id,
                        AccessPermission.cleanroom_id == room.id,
                    )
                )
                if not perm:
                    db.add(
                        AccessPermission(
                            user_id=target_user.id,
                            cleanroom_id=room.id,
                            enabled=True,
                        )
                    )
        db.commit()

        now_dt = datetime.now(timezone.utc)
        existing_events = db.scalar(select(func.count(AccessEvent.id))) or 0
        if existing_events == 0 and len(rooms) >= 2:
            sala_a, sala_b = rooms[0], rooms[1]
            dev_a, dev_b = devices_by_room[sala_a.id], devices_by_room[sala_b.id]
            demo_events = [
                (p1.id, sala_a.id, cards_by_user[p1.id].id, dev_a.id, "ENTRY", "ALLOWED", None, now_dt - timedelta(hours=8)),
                (p2.id, sala_a.id, cards_by_user[p2.id].id, dev_a.id, "ENTRY", "ALLOWED", None, now_dt - timedelta(hours=7, minutes=40)),
                (p2.id, sala_a.id, cards_by_user[p2.id].id, dev_a.id, "EXIT", "ALLOWED", None, now_dt - timedelta(hours=5, minutes=30)),
                (p1.id, sala_a.id, cards_by_user[p1.id].id, dev_a.id, "EXIT", "ALLOWED", None, now_dt - timedelta(hours=5)),
                (None, sala_a.id, None, dev_a.id, "ENTRY", "DENIED", "CARD_NOT_FOUND", now_dt - timedelta(hours=4, minutes=15)),
                (p1.id, sala_a.id, cards_by_user[p1.id].id, dev_a.id, "ENTRY", "ALLOWED", None, now_dt - timedelta(hours=2)),
                (p2.id, sala_b.id, cards_by_user[p2.id].id, dev_b.id, "ENTRY", "ALLOWED", None, now_dt - timedelta(hours=3)),
                (p2.id, sala_b.id, cards_by_user[p2.id].id, dev_b.id, "EXIT", "ALLOWED", None, now_dt - timedelta(hours=1, minutes=20)),
                (sup.id, sala_b.id, cards_by_user[sup.id].id, dev_b.id, "ENTRY", "ALLOWED", None, now_dt - timedelta(minutes=45)),
            ]
            for uid, cid, card_id, did, direction, result, reason, ts in demo_events:
                db.add(
                    AccessEvent(
                        user_id=uid,
                        cleanroom_id=cid,
                        rfid_card_id=card_id,
                        source_device_id=did,
                        direction=direction,
                        result=result,
                        denial_reason=reason,
                        occurred_at=ts,
                    )
                )
            db.commit()

        existing_readings = db.scalar(select(func.count(SensorReading.id))) or 0
        if existing_readings < 10 and len(rooms) >= 2:
            sala_a, sala_b = rooms[0], rooms[1]
            dev_a, dev_b = devices_by_room[sala_a.id], devices_by_room[sala_b.id]
            # 12 time points correlated with occupancy in SALA-A + baseline series in SALA-B
            series_a = [
                (9, 20.6, 43.2, 62.0, 12.6),
                (8, 21.1, 44.8, 124.0, 12.2),
                (7, 21.6, 46.1, 168.0, 11.8),
                (6, 21.4, 45.4, 145.0, 12.0),
                (5, 20.9, 44.0, 88.0, 12.5),
                (4, 20.7, 43.5, 72.0, 12.7),
                (3, 21.0, 44.2, 95.0, 12.4),
                (2, 21.5, 45.9, 134.0, 12.1),
                (1, 21.8, 46.5, 152.0, 11.9),
                (0, 21.3, 45.2, 118.0, 12.4),
            ]
            for hours_ago, temp, hum, pm25, press in series_a:
                ts = now_dt - timedelta(hours=hours_ago, minutes=10)
                for metric, val, unit in [
                    ("temperature", temp, "°C"),
                    ("humidity", hum, "%"),
                    ("pm25", pm25, "p/m³"),
                    ("pressure", press, "Pa"),
                ]:
                    db.add(
                        SensorReading(
                            device_id=dev_a.id,
                            cleanroom_id=sala_a.id,
                            metric=metric,
                            value=val,
                            unit=unit,
                            recorded_at=ts,
                        )
                    )
            for hours_ago in range(4, -1, -1):
                ts = now_dt - timedelta(hours=hours_ago, minutes=5)
                for metric, val, unit in [
                    ("temperature", 20.8 + hours_ago * 0.1, "°C"),
                    ("humidity", 44.5 + hours_ago * 0.4, "%"),
                    ("pm25", 92.0 + (4 - hours_ago) * 11.0, "p/m³"),
                    ("pressure", 12.1, "Pa"),
                ]:
                    db.add(
                        SensorReading(
                            device_id=dev_b.id,
                            cleanroom_id=sala_b.id,
                            metric=metric,
                            value=val,
                            unit=unit,
                            recorded_at=ts,
                        )
                    )
            db.commit()

        existing_decisions = db.scalar(select(func.count(AgentDecisionLog.id))) or 0
        if existing_decisions == 0 and len(rooms) >= 2:
            sala_a, sala_b = rooms[0], rooms[1]
            dev_a, dev_b = devices_by_room[sala_a.id], devices_by_room[sala_b.id]
            db.add_all(
                [
                    AgentDecisionLog(
                        cleanroom_id=sala_a.id,
                        device_id=dev_a.id,
                        user_id=p1.id,
                        rule_code="RN-08",
                        decision_type="ALLOW",
                        severity="INFO",
                        summary="Acceso ENTRY permitido en SALA-A (aforo 1/6)",
                        created_at=now_dt - timedelta(hours=2),
                    ),
                    AgentDecisionLog(
                        cleanroom_id=sala_a.id,
                        device_id=dev_a.id,
                        rule_code="RN-03",
                        decision_type="DENY",
                        severity="WARNING",
                        summary="Acceso ENTRY denegado en SALA-A: CARD_NOT_FOUND",
                        created_at=now_dt - timedelta(hours=4, minutes=15),
                    ),
                    AgentDecisionLog(
                        cleanroom_id=sala_b.id,
                        device_id=dev_b.id,
                        user_id=sup.id,
                        rule_code="RN-08",
                        decision_type="ALLOW",
                        severity="INFO",
                        summary="Acceso ENTRY permitido en SALA-B (aforo 1/8)",
                        created_at=now_dt - timedelta(minutes=45),
                    ),
                ]
            )
            db.commit()

        print("Seed completado. No se muestran contraseñas.")
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
