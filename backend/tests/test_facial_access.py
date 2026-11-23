import base64
import json
import os
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-123456")
os.environ.setdefault("RFID_HMAC_KEY", "test-rfid-key-123456")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin_123456!")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("CLEANSPACE_ADMIN_KEY", "test-admin-api-key-123456")
os.environ.setdefault("CLEANSPACE_DEVICE_KEY", "test-device-api-key-123456")

from app.core.face_biometrics import VisionPipeline
from app.db.database import SessionLocal, Base, engine
from app.main import app
from app.models import Employee, EmployeeBiometric, Cleanroom, DoorPermission


@pytest.fixture(scope="module")
def setup_database():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # Ensure test employee exists
    emp = db.get(Employee, 101)
    if not emp:
        emp = Employee(id=101, firstName="Test", lastName="Operator", department="Cuarto Limpio ISO 5", isActive=True)
        db.add(emp)
        db.commit()
    yield db
    db.close()


def create_synthetic_face_image(dark: bool = False, masked: bool = False) -> str:
    """Creates a synthetic test image with facial/periocular features."""
    img = np.full((320, 320, 3), 20 if dark else 200, dtype=np.uint8)

    # Face oval
    face_color = (40, 40, 40) if dark else (180, 210, 235)
    cv2.ellipse(img, (160, 160), (70, 95), 0, 0, 360, face_color, -1)

    # Eyes & Eyebrows
    eye_color = (150, 150, 150) if dark else (40, 40, 40)
    cv2.circle(img, (135, 140), 9, eye_color, -1)  # Right eye
    cv2.circle(img, (185, 140), 9, eye_color, -1)  # Left eye

    # Eyebrows
    cv2.line(img, (120, 125), (150, 125), eye_color, 4)
    cv2.line(img, (170, 125), (200, 125), eye_color, 4)

    # Nose
    cv2.circle(img, (160, 165), 5, eye_color, -1)

    if masked:
        # Cleanroom mask covering lower half of face (cyan/blue medical mask)
        cv2.rectangle(img, (100, 175), (220, 250), (220, 180, 70), -1)
    else:
        # Open mouth
        cv2.ellipse(img, (160, 205), (20, 10), 0, 0, 360, (50, 50, 180), -1)

    _, buffer = cv2.imencode(".jpg", img)
    return base64.b64encode(buffer).decode("utf-8")


def test_vision_pipeline_low_light_enhancement():
    pipeline = VisionPipeline.get_instance()

    # Dark frame
    dark_frame = np.full((100, 100, 3), 15, dtype=np.uint8)
    enhanced, active, original_l = pipeline.enhance_low_light(dark_frame)

    assert active is True
    assert original_l < 30.0
    assert np.mean(enhanced) > original_l * 2  # Boosted significantly


def test_facial_verify_no_face_detected():
    client = TestClient(app)
    # Blank black image with no face
    blank = np.zeros((200, 200, 3), dtype=np.uint8)
    _, buffer = cv2.imencode(".jpg", blank)
    b64_blank = base64.b64encode(buffer).decode("utf-8")

    resp = client.post(
        "/api/v1/facial/verify",
        json={"image_base64": b64_blank, "door_id": "entrada-principal"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["authorized"] is False
    assert data["reason"] == "no_face_detected"


def test_facial_enrollment_and_periocular_matching(setup_database):
    client = TestClient(app)
    db = setup_database

    # Create synthetic enrollment vectors for employee 101
    sample_periocular = [0.08] * 128
    sample_full = [0.08] * 128

    bio = EmployeeBiometric(
        employee_id=101,
        sample_type="masked_cleanroom",
        full_face_vector=json.dumps(sample_full),
        periocular_vector=json.dumps(sample_periocular),
        notes="Prueba sintética con cubrebocas",
        is_active=True,
    )
    db.add(bio)
    db.commit()
    db.refresh(bio)

    # Test listing biometrics endpoint
    list_resp = client.get("/api/v1/facial/biometrics/101")
    assert list_resp.status_code == 200
    bios = list_resp.json()
    assert len(bios) >= 1
    assert bios[0]["employee_id"] == 101

    # Test cosine similarity logic
    pipeline = VisionPipeline.get_instance()
    similarity = pipeline.cosine_similarity(sample_periocular, sample_periocular)
    assert round(similarity, 3) == 1.000

    # Delete test biometric
    del_resp = client.delete(f"/api/v1/facial/biometrics/{bio.id}")
    assert del_resp.status_code == 204
