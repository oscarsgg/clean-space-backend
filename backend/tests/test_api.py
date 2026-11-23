import os
from uuid import uuid4

os.environ["JWT_SECRET_KEY"] = "test-secret-key-123456"
os.environ["RFID_HMAC_KEY"] = "test-rfid-key-123456"
os.environ["INITIAL_ADMIN_PASSWORD"] = "Admin_123456!"
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["CLEANSPACE_ADMIN_KEY"] = "test-admin-api-key-123456"
os.environ["CLEANSPACE_DEVICE_KEY"] = "test-device-api-key-123456"

from sqlalchemy import select
from fastapi.testclient import TestClient
from app.core.config import get_settings
from app.core.security import hash_password

get_settings.cache_clear()

from app.db.database import SessionLocal
from app.main import app
from app.models import Role, User

def test_health():
    assert TestClient(app).get("/health").json()["status"] == "ok"

def test_existing_and_transferred_routes_are_registered():
    paths = app.openapi()["paths"]
    expected_paths = {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/roles",
        "/api/v1/personas",
        "/api/v1/personas/{id}",
        "/api/v1/personas/{id}/tarjetas",
        "/api/v1/cuartos",
        "/api/v1/cuartos/{id}",
        "/api/v1/cuartos/{id}/parametros",
        "/api/v1/dispositivos",
        "/api/v1/dispositivos/{id}",
        "/api/v1/dispositivos/{id}/llave",
        "/api/v1/tarjetas",
        "/api/v1/tarjetas/{id}",
        "/api/v1/permisos",
        "/api/v1/permisos/{id}",
        "/api/v1/iot/auth",
        "/api/v1/iot/heartbeat",
        "/api/v1/iot/telemetria",
        "/api/v1/iot/acceso/validar",
        "/api/v1/iot/simular-acceso",
        "/api/v1/iot/comandos/pendientes",
        "/api/v1/iot/comandos/{id}/ack",
        "/api/v1/comandos",
        "/api/v1/monitoreo/lecturas",
        "/api/v1/monitoreo/historico",
        "/api/v1/accesos",
        "/api/v1/avisos",
        "/api/v1/avisos/{id}/acknowledge",
        "/api/v1/analitica/etl/ejecutar",
        "/api/v1/analitica/modelo/entrenar",
        "/api/v1/analitica/modelo/metricas",
        "/api/v1/analitica/anomalias",
        "/api/v1/analitica/indicadores",
        "/api/v1/analitica/series",
        "/api/v1/analitica/correlacion",
        "/api/v1/analitica/patrones-acceso",
        "/api/v1/dashboard/supervisor/summary",
        "/api/v1/dashboard/admin/summary",
        "/api/v1/agente/decisiones",
        "/api/v1/agente/conversaciones",
        "/api/v1/agente/conversaciones/{id}",
        "/api/v1/agente/chat",
        "/api/v1/agente/mensajes/{id}/confirmar",
        "/api/v1/agente/reportes",
        "/api/v1/agente/reportes/{id}/export",
        "/api/v1/eventos/stream",
        "/api/v1/employees/",
        "/api/v1/rfid-tags/",
        "/api/v1/access-logs/",
        "/api/v1/permissions/",
        "/api/v1/permissions/{permission_id}",
        "/api/v1/device/sync",
        "/api/v1/device/access",
        "/api/v1/device/events",
    }
    assert expected_paths <= paths.keys()

def test_login_accepts_oauth2_form():
    db = SessionLocal()
    role = db.scalar(select(Role).where(Role.code == "ADMINISTRADOR"))
    if not role:
        role = Role(code="ADMINISTRADOR", name="Administrador")
        db.add(role)
        db.commit()
        db.refresh(role)
    if not db.scalar(select(User).where(User.email == "admin@cleanspace.com")):
        user = User(email="admin@cleanspace.com", password_hash=hash_password("Admin_123456!"), full_name="Test Admin")
        user.roles = [role]
        db.add(user)
        db.commit()
    db.close()

    response = TestClient(app).post("/api/v1/auth/login", data={"username": "admin@cleanspace.com", "password": "Admin_123456!"})
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "admin@cleanspace.com"

def test_employee_and_rfid_tag_crud():
    client = TestClient(app)
    employee_response = client.post("/api/v1/employees/", json={
        "firstName": "Ana",
        "lastName": "Lopez",
        "department": "Limpieza",
    })
    assert employee_response.status_code == 201
    employee = employee_response.json()
    assert employee["isActive"] is True

    uid = f"A1:{uuid4().hex[:8]}"
    tag_response = client.post("/api/v1/rfid-tags/", json={
        "uid": uid,
        "employee_id": employee["id"],
    })
    assert tag_response.status_code == 201
    assert tag_response.json()["uid"] == uid.replace(":", "").upper()

    get_response = client.get(f"/api/v1/rfid-tags/{uid.lower()}")
    assert get_response.status_code == 200
    assert get_response.json()["employee_id"] == employee["id"]

    update_response = client.put(
        f"/api/v1/rfid-tags/{uid}",
        json={"isActive": False},
    )
    assert update_response.status_code == 200
    assert update_response.json()["isActive"] is False

def test_access_logs_permissions_and_device_endpoints():
    client = TestClient(app)
    admin_headers = {"X-Admin-Key": "test-admin-api-key-123456"}
    device_headers = {"X-Device-Key": "test-device-api-key-123456"}

    employee_response = client.post("/api/v1/employees/", json={
        "firstName": "Eva",
        "lastName": "Ruiz",
        "department": "Operaciones",
    })
    assert employee_response.status_code == 201
    employee_id = employee_response.json()["id"]

    uid = f"B2:{uuid4().hex[:8]}"
    normalized_uid = uid.replace(":", "").upper()
    tag_response = client.post("/api/v1/rfid-tags/", json={
        "uid": uid,
        "employee_id": employee_id,
    })
    assert tag_response.status_code == 201

    permission_response = client.post(
        "/api/v1/permissions/",
        headers=admin_headers,
        json={"rfid_uid": uid, "door_id": "DOOR-1"},
    )
    assert permission_response.status_code == 201
    permission_id = permission_response.json()["id"]
    assert permission_response.json()["rfid_uid"] == normalized_uid

    assert client.get("/api/v1/permissions/").status_code == 401
    assert client.get("/api/v1/permissions/", headers=admin_headers).status_code == 200
    sync_response = client.get(
        "/api/v1/device/sync",
        params={"door_id": "DOOR-1"},
        headers=device_headers,
    )
    assert sync_response.status_code == 200
    assert normalized_uid in sync_response.json()["allowed_uids"]

    access_response = client.post(
        "/api/v1/device/access",
        headers=device_headers,
        json={"uid": uid, "door_id": "DOOR-1"},
    )
    assert access_response.status_code == 200
    assert access_response.json()["authorized"] is True

    log_response = client.post("/api/v1/access-logs/", json={
        "rfid_uid": normalized_uid,
        "render_id": "DOOR-1",
        "access_status": "granted",
    })
    assert log_response.status_code == 201
    assert client.get("/api/v1/access-logs/").status_code == 200

    offline_response = client.post(
        "/api/v1/device/events",
        headers=device_headers,
        json={"events": [{
            "uid": uid,
            "door_id": "DOOR-1",
            "access_status": "offline_allowed",
            "created_at": "2026-11-08",
        }]},
    )
    assert offline_response.status_code == 202
    assert offline_response.json() == {"accepted": 1}

    update_response = client.patch(
        f"/api/v1/permissions/{permission_id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert update_response.status_code == 200
    assert update_response.json()["is_active"] is False

    delete_response = client.delete(
        f"/api/v1/permissions/{permission_id}",
        headers=admin_headers,
    )
    assert delete_response.status_code == 204


def _ensure_role(db, code: str, name: str) -> Role:
    role = db.scalar(select(Role).where(Role.code == code))
    if not role:
        role = Role(code=code, name=name)
        db.add(role)
        db.commit()
        db.refresh(role)
    return role


def _setup_supervisor_fixture():
    from datetime import datetime, timedelta, timezone
    from app.core.security import create_token, hash_refresh, hash_rfid
    from app.models import (
        AccessEvent,
        AccessPermission,
        Cleanroom,
        Device,
        MonitoringParameter,
        RfidCard,
        SensorReading,
    )

    db = SessionLocal()
    try:
        sup_role = _ensure_role(db, "SUPERVISOR", "Supervisor")
        op_role = _ensure_role(db, "PERSONAL_AUTORIZADO", "Personal autorizado")
        suffix = uuid4().hex[:6]

        room = Cleanroom(
            code=f"SALA-T-{suffix}",
            name=f"Sala Test {suffix}",
            location="Lab",
            capacity=1,
            iso_class="ISO 5",
        )
        db.add(room)
        db.flush()

        dev_key = f"dev-key-{suffix}"
        device = Device(
            cleanroom_id=room.id,
            code=f"DEV-T-{suffix}",
            type="SENSOR",
            serial_number=f"SER-T-{suffix}",
            api_key_hash=hash_refresh(dev_key),
        )
        db.add(device)
        db.flush()

        db.add(
            MonitoringParameter(
                cleanroom_id=room.id,
                metric="pm25",
                min_value=0.0,
                max_value=200.0,
                unit="p/m³",
                enabled=True,
            )
        )

        sup = User(
            email=f"sup-{suffix}@cleanspace.local",
            password_hash=hash_password("Supervisor_123!"),
            full_name="Supervisor Test",
        )
        sup.roles = [sup_role]
        sup.assigned_cleanrooms = [room]
        db.add(sup)

        op1 = User(
            email=f"op1-{suffix}@cleanspace.local",
            password_hash=hash_password("Personal_123!"),
            full_name="Operador Uno",
        )
        op1.roles = [op_role]
        db.add(op1)

        op2 = User(
            email=f"op2-{suffix}@cleanspace.local",
            password_hash=hash_password("Personal_123!"),
            full_name="Operador Dos",
        )
        op2.roles = [op_role]
        db.add(op2)
        db.flush()

        uid1 = f"U1-{suffix}"
        uid2 = f"U2-{suffix}"
        db.add(RfidCard(user_id=op1.id, uid_hash=hash_rfid(uid1), label=uid1))
        db.add(RfidCard(user_id=op2.id, uid_hash=hash_rfid(uid2), label=uid2))
        db.add(AccessPermission(user_id=op1.id, cleanroom_id=room.id, enabled=True))
        db.add(AccessPermission(user_id=op2.id, cleanroom_id=room.id, enabled=True))

        now_dt = datetime.now(timezone.utc)
        for idx, h in enumerate([10, 8, 5, 3, 2, 1]):
            db.add(
                SensorReading(
                    device_id=device.id,
                    cleanroom_id=room.id,
                    metric="pm25",
                    value=60.0 + idx * 15.0,
                    unit="p/m³",
                    recorded_at=now_dt - timedelta(hours=h),
                )
            )
        for h in [10, 7, 4]:
            db.add(
                AccessEvent(
                    user_id=op1.id,
                    cleanroom_id=room.id,
                    source_device_id=device.id,
                    direction="ENTRY",
                    result="ALLOWED",
                    occurred_at=now_dt - timedelta(hours=h),
                )
            )
            db.add(
                AccessEvent(
                    user_id=op1.id,
                    cleanroom_id=room.id,
                    source_device_id=device.id,
                    direction="EXIT",
                    result="ALLOWED",
                    occurred_at=now_dt - timedelta(hours=h - 1),
                )
            )
        db.commit()

        sup_token = create_token(str(sup.id), "access", timedelta(minutes=30), ["SUPERVISOR"])
        dev_token = create_token(str(device.id), "device", timedelta(minutes=30), ["DEVICE"])
        return {
            "room_id": room.id,
            "room_code": room.code,
            "device_id": device.id,
            "uid1": uid1,
            "uid2": uid2,
            "sup_headers": {"Authorization": f"Bearer {sup_token}"},
            "dev_headers": {"Authorization": f"Bearer {dev_token}"},
        }
    finally:
        db.close()


def test_room_capacity_stay_duration_and_three_strike_lockout():
    from app.models import AgentDecisionLog, Alert, Command

    fx = _setup_supervisor_fixture()
    client = TestClient(app)

    r1 = client.post(
        "/api/v1/iot/acceso/validar",
        headers=fx["dev_headers"],
        json={"device_id": fx["device_id"], "uid": fx["uid1"], "direction": "ENTRY"},
    )
    assert r1.status_code == 200
    assert r1.json()["allowed"] is True
    assert r1.json()["current_occupancy"] == 1

    r2 = client.post(
        "/api/v1/iot/acceso/validar",
        headers=fx["dev_headers"],
        json={"device_id": fx["device_id"], "uid": fx["uid2"], "direction": "ENTRY"},
    )
    assert r2.status_code == 200
    assert r2.json()["allowed"] is False
    assert r2.json()["reason"] == "ROOM_FULL"

    r3 = client.post(
        "/api/v1/iot/acceso/validar",
        headers=fx["dev_headers"],
        json={"device_id": fx["device_id"], "uid": fx["uid1"], "direction": "EXIT"},
    )
    assert r3.status_code == 200
    assert r3.json()["allowed"] is True
    assert r3.json()["stay_minutes"] is not None
    assert r3.json()["current_occupancy"] == 0

    r4 = client.post(
        "/api/v1/iot/acceso/validar",
        headers=fx["dev_headers"],
        json={"device_id": fx["device_id"], "uid": fx["uid2"], "direction": "ENTRY"},
    )
    assert r4.status_code == 200
    assert r4.json()["allowed"] is True

    # 3 consecutive denied attempts on device -> auto lock
    for attempt in range(3):
        denied_resp = client.post(
            "/api/v1/iot/acceso/validar",
            headers=fx["dev_headers"],
            json={"device_id": fx["device_id"], "uid": f"BAD-{attempt}", "direction": "ENTRY"},
        )
        assert denied_resp.status_code == 200
        assert denied_resp.json()["allowed"] is False
        assert denied_resp.json()["auto_locked"] is (attempt == 2)

    db = SessionLocal()
    try:
        lock_alert = db.scalar(
            select(Alert).where(Alert.device_id == fx["device_id"], Alert.type == "SECURITY_LOCKOUT")
        )
        assert lock_alert is not None
        assert lock_alert.severity == "CRITICAL"

        lock_cmd = db.scalar(
            select(Command).where(Command.device_id == fx["device_id"], Command.command == "bloquear")
        )
        assert lock_cmd is not None

        lock_dec = db.scalar(
            select(AgentDecisionLog).where(
                AgentDecisionLog.device_id == fx["device_id"],
                AgentDecisionLog.rule_code == "RN-10",
                AgentDecisionLog.decision_type == "LOCK",
            )
        )
        assert lock_dec is not None
    finally:
        db.close()


def test_simular_acceso_and_out_of_range_telemetry():
    from app.models import AgentDecisionLog

    fx = _setup_supervisor_fixture()
    client = TestClient(app)

    sim_resp = client.post(
        "/api/v1/iot/simular-acceso",
        headers=fx["sup_headers"],
        json={"cleanroom_id": fx["room_id"], "uid": fx["uid1"], "direction": "ENTRY"},
    )
    assert sim_resp.status_code == 200
    assert sim_resp.json()["allowed"] is True

    reading_resp = client.post(
        "/api/v1/monitoreo/lecturas",
        headers=fx["sup_headers"],
        json={
            "device_id": fx["device_id"],
            "cleanroom_id": fx["room_id"],
            "metric": "pm25",
            "value": 310.0,
            "unit": "p/m³",
        },
    )
    assert reading_resp.status_code == 201

    db = SessionLocal()
    try:
        rn11 = db.scalar(
            select(AgentDecisionLog).where(
                AgentDecisionLog.device_id == fx["device_id"],
                AgentDecisionLog.rule_code == "RN-11",
                AgentDecisionLog.decision_type == "ALERT",
            )
        )
        assert rn11 is not None
    finally:
        db.close()


def test_agent_chat_confirm_decisions_and_reports():
    fx = _setup_supervisor_fixture()
    client = TestClient(app)

    chat1 = client.post(
        "/api/v1/agente/chat",
        headers=fx["sup_headers"],
        json={"message": "Cuál es la ocupación actual?", "cleanroom_id": fx["room_id"]},
    )
    assert chat1.status_code == 200
    body1 = chat1.json()
    assert body1["agent_message"]["intent"] == "OCCUPANCY_STATUS"
    assert body1["agent_message"]["confirmation_status"] == "NONE"
    conv_id = body1["conversation_id"]

    chat2 = client.post(
        "/api/v1/agente/chat",
        headers=fx["sup_headers"],
        json={"message": f"Bloquear {fx['room_code']}", "conversation_id": conv_id},
    )
    assert chat2.status_code == 200
    body2 = chat2.json()
    assert body2["agent_message"]["pending_command"]["command"] == "bloquear"
    assert body2["agent_message"]["confirmation_status"] == "PENDING"
    msg_id = body2["agent_message"]["id"]

    confirm_resp = client.post(
        f"/api/v1/agente/mensajes/{msg_id}/confirmar",
        headers=fx["sup_headers"],
        json={"action": "CONFIRM"},
    )
    assert confirm_resp.status_code == 200
    conf_data = confirm_resp.json()
    assert conf_data["confirmation_status"] == "CONFIRMED"
    assert conf_data["command_id"] is not None
    assert conf_data["decision_id"] is not None

    convs = client.get("/api/v1/agente/conversaciones", headers=fx["sup_headers"])
    assert convs.status_code == 200
    assert any(c["id"] == conv_id for c in convs.json())

    conv_detail = client.get(f"/api/v1/agente/conversaciones/{conv_id}", headers=fx["sup_headers"])
    assert conv_detail.status_code == 200
    assert len(conv_detail.json()["messages"]) == 4

    decs = client.get(
        f"/api/v1/agente/decisiones?cleanroom_id={fx['room_id']}&decision_type=COMMAND",
        headers=fx["sup_headers"],
    )
    assert decs.status_code == 200
    assert decs.json()["total"] >= 1

    rep = client.post(
        "/api/v1/agente/reportes",
        headers=fx["sup_headers"],
        json={"cleanroom_id": fx["room_id"]},
    )
    assert rep.status_code == 201
    rep_id = rep.json()["id"]

    for fmt in ("json", "md", "csv"):
        exp = client.get(
            f"/api/v1/agente/reportes/{rep_id}/export?format={fmt}",
            headers=fx["sup_headers"],
        )
        assert exp.status_code == 200
        assert len(exp.text) > 10


def test_supervisor_summary_ecbd_and_realtime_broker():
    import asyncio
    from app.services.realtime import RealtimeBroker

    fx = _setup_supervisor_fixture()
    client = TestClient(app)

    summary = client.get("/api/v1/dashboard/supervisor/summary", headers=fx["sup_headers"])
    assert summary.status_code == 200
    sdata = summary.json()
    assert "total_events" in sdata and "rooms" in sdata
    room_card = next(r for r in sdata["rooms"] if r["id"] == fx["room_id"])
    assert room_card["iso_class"] == "ISO 5"
    assert room_card["capacity"] == 1
    assert "pm25" in room_card["metrics"]

    corr = client.get(
        f"/api/v1/analitica/correlacion?cleanroom_id={fx['room_id']}",
        headers=fx["sup_headers"],
    )
    assert corr.status_code == 200
    cdata = corr.json()
    assert "coefficient_r" in cdata
    assert "strength" in cdata
    assert len(cdata["points"]) >= 6

    patterns = client.get("/api/v1/analitica/patrones-acceso", headers=fx["sup_headers"])
    assert patterns.status_code == 200
    pdata = patterns.json()
    assert len(pdata) >= 3
    assert all(p["cluster"] in {"c1", "c2", "c3"} for p in pdata)

    train = client.post("/api/v1/analitica/modelo/entrenar", headers=fx["sup_headers"])
    assert train.status_code == 200
    assert train.json()["model_type"] == "isolation_forest"
    assert "anomalies_detected" in train.json()["result_json"]

    # SSE endpoint rejects missing token
    assert client.get("/api/v1/eventos/stream").status_code == 401

    # RealtimeBroker pub/sub verification
    async def _check_broker():
        broker = RealtimeBroker()
        async with broker.subscription() as q:
            broker.publish("access_event", {"id": 99})
            frame = await q.get()
            assert "event: access_event" in frame
            assert '"id": 99' in frame

    asyncio.run(_check_broker())
