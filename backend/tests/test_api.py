import os
from uuid import uuid4

os.environ.setdefault("JWT_SECRET_KEY","test-secret-key-123456")
os.environ.setdefault("RFID_HMAC_KEY","test-rfid-key-123456")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD","Admin_123456!")
os.environ.setdefault("DATABASE_URL","sqlite:///./test.db")
os.environ.setdefault("CLEANSPACE_ADMIN_KEY", "test-admin-api-key-123456")
os.environ.setdefault("CLEANSPACE_DEVICE_KEY", "test-device-api-key-123456")
from sqlalchemy import select
from fastapi.testclient import TestClient
from app.core.security import hash_password
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
        "/api/v1/analitica/patrones-acceso",
        "/api/v1/dashboard/admin/summary",
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
