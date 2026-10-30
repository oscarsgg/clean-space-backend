import os
os.environ.setdefault("JWT_SECRET_KEY","test-secret-key-123456")
os.environ.setdefault("RFID_HMAC_KEY","test-rfid-key-123456")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD","Admin_123456!")
os.environ.setdefault("DATABASE_URL","sqlite:///./test.db")
from sqlalchemy import select
from fastapi.testclient import TestClient
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.models.models import Role, User

def test_health():
    assert TestClient(app).get("/health").json()["status"] == "ok"

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
