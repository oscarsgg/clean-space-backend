import os
import sys

# Ensure backend root is in sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from datetime import date
from sqlalchemy import select
from app.db.database import SessionLocal, Base, engine
from app.models import Employee, Cleanroom, DoorPermission, RfidTag

def seed_employees():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Check or create Cleanroom
        sala = db.scalar(select(Cleanroom).where(Cleanroom.code == "SALA-A"))
        if not sala:
            sala = Cleanroom(name="Laboratorio Principal Cuarto Limpio", code="SALA-A", location="Edificio A - Nivel 2")
            db.add(sala)
            db.commit()
            db.refresh(sala)

        sample_employees = [
            {"id": 1, "firstName": "Carlos", "lastName": "Mendez", "department": "Operaciones Cuarto Limpio"},
            {"id": 2, "firstName": "Elena", "lastName": "Silva", "department": "Biotecnología y Calidad"},
            {"id": 3, "firstName": "Roberto", "lastName": "Garza", "department": "Mantenimiento HVAC ISO 5"},
            {"id": 4, "firstName": "Mariana", "lastName": "Torres", "department": "Supervisión Esterilización"},
        ]

        for emp_data in sample_employees:
            emp = db.get(Employee, emp_data["id"])
            if not emp:
                emp = Employee(
                    id=emp_data["id"],
                    firstName=emp_data["firstName"],
                    lastName=emp_data["lastName"],
                    department=emp_data["department"],
                    isActive=True,
                    created_at=date.today(),
                )
                db.add(emp)
            else:
                emp.firstName = emp_data["firstName"]
                emp.lastName = emp_data["lastName"]
                emp.department = emp_data["department"]
                emp.isActive = True

        db.commit()

        # Add DoorPermissions for door 'entrada-principal'
        for emp_data in sample_employees:
            tag_uid = f"CARD-EMP-{emp_data['id']:03d}"
            tag = db.get(RfidTag, tag_uid)
            if not tag:
                tag = RfidTag(uid=tag_uid, employee_id=emp_data["id"], isActive=True, assigned_date=date.today())
                db.add(tag)
                db.commit()

            door_perm = db.scalar(
                select(DoorPermission).where(
                    DoorPermission.rfid_uid == tag_uid,
                    DoorPermission.door_id == "entrada-principal"
                )
            )
            if not door_perm:
                db.add(DoorPermission(rfid_uid=tag_uid, door_id="entrada-principal", is_active=True))

        db.commit()
        print("Empleados de cuarto limpio y permisos para 'entrada-principal' configurados con éxito.")

        employees = db.query(Employee).all()
        for e in employees:
            print(f" - [{e.id}] {e.firstName} {e.lastName} | Dept: {e.department}")

    finally:
        db.close()

if __name__ == "__main__":
    seed_employees()
