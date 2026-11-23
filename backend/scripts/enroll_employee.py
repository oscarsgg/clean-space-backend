import base64
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import cv2
import numpy as np

# Ensure backend root is in sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.face_biometrics import VisionPipeline
from app.db.database import Base, SessionLocal, engine
from app.models import Cleanroom, DoorPermission, Employee, EmployeeBiometric, RfidTag


def list_existing_employees(db):
    print("\n--- Empleados Actuales en el Sistema ---")
    employees = db.query(Employee).filter(Employee.isActive.is_(True)).all()
    if not employees:
        print(" (No hay empleados registrados)")
        return employees

    for emp in employees:
        bios = db.query(EmployeeBiometric).filter(
            EmployeeBiometric.employee_id == emp.id,
            EmployeeBiometric.is_active.is_(True)
        ).all()
        bio_info = f"[{len(bios)} muestra(s) biométrica(s)]" if bios else "[SIN BIOMETRÍA REGISTRADA]"
        print(f" ID: {emp.id:2d} | {emp.firstName} {emp.lastName:15s} | Dept: {emp.department:25s} | {bio_info}")
    print("----------------------------------------\n")
    return employees


def capture_face_from_webcam(pipeline: VisionPipeline, prompt_title: str) -> np.ndarray:
    """Opens webcam preview window and lets the user capture their face by pressing SPACE."""
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        raise RuntimeError("No se pudo acceder a la cámara web (índice 0).")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    window_name = f"CleanSpace - Enrolamiento Facial [{prompt_title}]"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 800, 600)

    print(f"\n[*] Abriendo cámara para: {prompt_title}")
    print("  -> Mira a la cámara.")
    print("  -> Presiona [ESPACIO] cuando estés listo para tomar la foto.")
    print("  -> Presiona [ESC] o [Q] para cancelar.")

    captured_frame = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue

            display = frame.copy()
            h, w = frame.shape[:2]

            # Detect face & eyes
            faces, low_light, l_mean = pipeline.detect_faces(frame)
            has_face = faces is not None and len(faces) > 0

            # UI Guide Box
            color = (0, 255, 0) if has_face else (0, 165, 255)

            if has_face:
                best_face = max(faces, key=lambda f: f[14])
                fx, fy, fw, fh = int(best_face[0]), int(best_face[1]), int(best_face[2]), int(best_face[3])
                rx, ry = int(best_face[4]), int(best_face[5])
                lx, ly = int(best_face[6]), int(best_face[7])

                # Draw bounding box and eye landmarks
                cv2.rectangle(display, (fx, fy), (fx + fw, fy + fh), color, 2)
                cv2.circle(display, (rx, ry), 5, (0, 255, 255), -1)
                cv2.circle(display, (lx, ly), 5, (0, 255, 255), -1)

                cv2.putText(
                    display,
                    "ROSTRO Y OJOS DETECTADOS - PRESIONA [ESPACIO]",
                    (15, h - 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )
            else:
                cv2.putText(
                    display,
                    "Centra tu rostro frente a la camara...",
                    (15, h - 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 165, 255),
                    2,
                )

            # Top instructions bar
            top_bar = display.copy()
            cv2.rectangle(top_bar, (0, 0), (w, 50), (20, 20, 20), -1)
            cv2.addWeighted(top_bar, 0.7, display, 0.3, 0, display)
            cv2.putText(
                display,
                prompt_title,
                (15, 32),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )

            cv2.imshow(window_name, display)
            key = cv2.waitKey(1) & 0xFF
            if key == 32:  # Space
                if has_face:
                    captured_frame = frame.copy()
                    print("[*] ¡Foto capturada con éxito!")
                    break
                else:
                    print("[!] No se detectó un rostro claro. Asegúrate de mirar a la cámara y vuelve a presionar [ESPACIO].")
            elif key == ord("q") or key == ord("Q") or key == 27:
                print("[*] Captura cancelada por el usuario.")
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    return captured_frame


def main():
    print("=================================================================")
    print("    ASISTENTE DE REGISTRO BIOMÉTRICO FACIAL - CLEANSPACE        ")
    print("=================================================================")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    pipeline = VisionPipeline.get_instance()

    try:
        employees = list_existing_employees(db)

        print("¿Qué deseas hacer?")
        print(" 1) Crear un NUEVO empleado y registrar su cara")
        print(" 2) Asignar cara a un empleado EXISTENTE")
        print(" 3) Salir")

        opt = input("\nSelecciona una opción [1, 2 o 3]: ").strip()

        if opt == "1":
            print("\n--- Datos del Nuevo Empleado ---")
            first_name = input("Nombre (ej. Juan): ").strip()
            if not first_name:
                print("[!] El nombre no puede estar vacío.")
                return

            last_name = input("Apellido (ej. Pérez): ").strip()
            if not last_name:
                last_name = "Operador"

            department = input("Departamento (ej. Cuarto Limpio ISO 5 / Biotecnología): ").strip()
            if not department:
                department = "Cuarto Limpio"

            # Create employee
            emp = Employee(
                firstName=first_name,
                lastName=last_name,
                department=department,
                isActive=True,
                created_at=date.today(),
            )
            db.add(emp)
            db.commit()
            db.refresh(emp)

            # Assign RFID virtual tag and door permission for 'entrada-principal'
            tag_uid = f"CARD-EMP-{emp.id:03d}"
            tag = RfidTag(uid=tag_uid, employee_id=emp.id, isActive=True, assigned_date=date.today())
            db.add(tag)
            db.add(DoorPermission(rfid_uid=tag_uid, door_id="entrada-principal", is_active=True))
            db.commit()

            print(f"\n[OK] Empleado #{emp.id} ({emp.firstName} {emp.lastName}) creado con permisos para 'entrada-principal'.")

        elif opt == "2":
            emp_id_str = input("\nIngresa el ID del empleado existente (ej. 1): ").strip()
            if not emp_id_str or not emp_id_str.isdigit():
                print("[!] ID inválido.")
                return

            emp = db.get(Employee, int(emp_id_str))
            if not emp:
                print(f"[!] No existe un empleado con ID {emp_id_str}.")
                return

            print(f"[OK] Empleado seleccionado: {emp.firstName} {emp.lastName} ({emp.department})")

        else:
            print("Operación finalizada.")
            return

        # Choose enrollment mode
        print("\n¿Cómo deseas registrar la muestra biométrica?")
        print(" 1) Rostro Completo (Sin cubrebocas)")
        print(" 2) Cuarto Limpio (Con cubrebocas / cofia)")
        print(" 3) AMBAS (Recomendado para Cuartos Limpios: foto normal + foto con cubrebocas)")
        sample_opt = input("Opción [1, 2 o 3]: ").strip() or "3"

        samples_to_take = []
        if sample_opt == "1":
            samples_to_take.append(("full_face", f"Rostro Completo - {emp.firstName} {emp.lastName}"))
        elif sample_opt == "2":
            samples_to_take.append(("masked_cleanroom", f"Cuarto Limpio (Con Cubrebocas) - {emp.firstName} {emp.lastName}"))
        else:
            samples_to_take.append(("full_face", f"Paso 1/2: Rostro Completo (Sin Cubrebocas) - {emp.firstName}"))
            samples_to_take.append(("masked_cleanroom", f"Paso 2/2: Cuarto Limpio (Ponte tu cubrebocas) - {emp.firstName}"))

        for sample_type, title in samples_to_take:
            frame = capture_face_from_webcam(pipeline, title)
            if frame is None:
                print("[!] Se canceló la captura de esta muestra.")
                continue

            faces, low_light, _ = pipeline.detect_faces(frame)
            if faces is None or len(faces) == 0:
                print("[!] No se detectaron características faciales. No se pudo guardar esta muestra.")
                continue

            best_face = max(faces, key=lambda f: f[14])
            features = pipeline.extract_features(frame, best_face)

            biometric = EmployeeBiometric(
                employee_id=emp.id,
                sample_type=sample_type,
                full_face_vector=json.dumps(features["full_face_vector"]),
                periocular_vector=json.dumps(features["periocular_vector"]),
                notes=f"Enrolamiento guiado ({sample_type}, mascara={features['is_masked']}, luz_baja={low_light})",
                is_active=True,
            )
            db.add(biometric)
            db.commit()
            print(f"[EXITO] Muestra biométrica guardada ({sample_type}). Puntos perioculares extraídos.")

        print("\n=================================================================")
        print(f" ¡LISTO! {emp.firstName} {emp.lastName} ya está registrado en CleanSpace.")
        print(" Ahora puedes ejecutar 'python facial_camera_agent.py'")
        print(" Al pararte frente a la cámara, el sistema te reconocerá y")
        print(" mostrará 'ACCESO AUTORIZADO' con la puerta desbloqueada.")
        print("=================================================================\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
