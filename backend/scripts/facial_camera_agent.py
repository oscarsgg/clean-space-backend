import base64
import json
import math
import os
import sys
import time
import urllib
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.face_biometrics import VisionPipeline

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000/api/v1")
DOOR_ID = os.getenv("DOOR_ID", "entrada-principal")
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))


def http_post_json(url: str, payload: dict, timeout: float = 3.0):
    """
    Sends a POST request with JSON payload using httpx, requests,
    or standard library urllib.request as fallback.
    Returns (status_code, response_data).
    """
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            data = resp.json() if resp.status_code in (200, 201) else resp.text
            return resp.status_code, data
    except ImportError:
        pass
    except Exception as e:
        return 0, str(e)

    try:
        import requests
        resp = requests.post(url, json=payload, timeout=timeout)
        data = resp.json() if resp.status_code in (200, 201) else resp.text
        return resp.status_code, data
    except ImportError:
        pass
    except Exception as e:
        return 0, str(e)

    try:
        import json
        import urllib.request
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status, body
    except Exception as e:
        return 0, str(e)


class FacialCameraAgent:
    def __init__(self, api_url: str = API_BASE_URL, door_id: str = DOOR_ID, cam_idx: int = CAMERA_INDEX):
        self.api_url = api_url
        self.door_id = door_id
        self.cam_idx = cam_idx

        # Initialize local vision pipeline
        self.pipeline = VisionPipeline.get_instance()

        # State tracking
        self.last_verify_time = 0.0
        self.verify_cooldown = 1.2  # seconds between auto-verifications
        self.last_result: Optional[dict] = None
        self.last_result_expiry = 0.0
        self.screen_light_enabled = False
        self.auto_verify = True

    def toggle_screen_light(self):
        self.screen_light_enabled = not self.screen_light_enabled
        print(f"[*] Luz de monitor: {'ACTIVADA' if self.screen_light_enabled else 'DESACTIVADA'}")

    def call_verify_api(self, frame: np.ndarray) -> dict:
        """Sends frame to backend verify endpoint. Falls back to local pipeline if API is offline."""
        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        b64_image = base64.b64encode(buffer).decode("utf-8")

        status_code, data = http_post_json(
            f"{self.api_url}/facial/verify",
            {"image_base64": b64_image, "door_id": self.door_id},
            timeout=2.5,
        )
        if status_code == 200 and isinstance(data, dict):
            return data

        # Fallback: direct local DB query
        return self._local_verify(frame)

    def _local_verify(self, frame: np.ndarray) -> dict:
        """Local fallback verification directly using SQLAlchemy."""
        try:
            from app.db.database import SessionLocal
            from app.models import Employee, EmployeeBiometric, DoorPermission, RfidTag, AccessLog
            import json

            db = SessionLocal()
            try:
                faces, low_light, _ = self.pipeline.detect_faces(frame)
                if faces is None or len(faces) == 0:
                    return {"authorized": False, "reason": "no_face_detected", "door_id": self.door_id}

                best_face = max(faces, key=lambda f: f[14])
                cand = self.pipeline.extract_features(frame, best_face)
                is_masked = cand["is_masked"]
                cand_periocular = cand["periocular_vector"]
                cand_full = cand["full_face_vector"]

                biometrics = db.query(EmployeeBiometric).filter(EmployeeBiometric.is_active.is_(True)).all()
                best_score = -1.0
                best_bio = None
                best_mode = "NONE"

                THRESHOLD_FULL_FACE = 0.42
                THRESHOLD_PERIOCULAR = 0.48

                for bio in biometrics:
                    if is_masked:
                        if bio.periocular_vector:
                            score = self.pipeline.cosine_similarity(cand_periocular, json.loads(bio.periocular_vector))
                            mode = "PERIOCULAR_EYES"
                            if score > best_score:
                                best_score = score
                                best_mode = mode
                                if score >= THRESHOLD_PERIOCULAR:
                                    best_bio = bio
                    else:
                        if bio.full_face_vector:
                            score = self.pipeline.cosine_similarity(cand_full, json.loads(bio.full_face_vector))
                            mode = "FULL_FACE"
                            if score > best_score:
                                best_score = score
                                best_mode = mode
                                if score >= THRESHOLD_FULL_FACE:
                                    best_bio = bio
                        elif bio.periocular_vector and bio.sample_type == "masked_cleanroom":
                            score = self.pipeline.cosine_similarity(cand_periocular, json.loads(bio.periocular_vector))
                            mode = "PERIOCULAR_EYES"
                            if score > best_score:
                                best_score = score
                                best_mode = mode
                                if score >= THRESHOLD_PERIOCULAR:
                                    best_bio = bio

                if best_bio:
                    emp = db.get(Employee, best_bio.employee_id)
                    if emp and emp.isActive:
                        return {
                            "authorized": True,
                            "reason": "allowed",
                            "employee_id": emp.id,
                            "employee_name": f"{emp.firstName} {emp.lastName}",
                            "department": emp.department,
                            "confidence_score": round(best_score, 4),
                            "match_mode": best_mode,
                            "is_masked": is_masked,
                            "low_light_enhanced": low_light,
                            "door_id": self.door_id,
                            "door_unlocked": True,
                        }

                return {
                    "authorized": False,
                    "reason": "unknown_face",
                    "confidence_score": round(best_score, 4),
                    "match_mode": best_mode,
                    "is_masked": is_masked,
                    "low_light_enhanced": low_light,
                    "door_id": self.door_id,
                }
            finally:
                db.close()
        except Exception as e:
            return {"authorized": False, "reason": f"error: {str(e)}", "door_id": self.door_id}

    def enroll_face_interactively(self, frame: np.ndarray):
        """Captures current frame and enrolls an employee."""
        print("\n" + "=" * 50)
        print(">>> MODO DE REGISTRO BIOMÉTRICO (CUARTO LIMPIO) <<<")
        try:
            emp_id_str = input("Ingrese el ID del empleado a enrolar (ej. 1, 2, 3, 4) o Enter para cancelar: ").strip()
            if not emp_id_str:
                print("Registro cancelado.")
                return

            emp_id = int(emp_id_str)
            print("Seleccione tipo de muestra:")
            print(" 1) Rostro Completo Estándar")
            print(" 2) Cuarto Limpio (Con Cubrebocas / Cofia)")
            choice = input("Opción [1 o 2, default 2]: ").strip()
            sample_type = "masked_cleanroom" if choice == "2" else "full_face"

            _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            b64_image = base64.b64encode(buffer).decode("utf-8")

            # Try API first
            status_code, data = http_post_json(
                f"{self.api_url}/facial/enroll",
                {
                    "employee_id": emp_id,
                    "image_base64": b64_image,
                    "sample_type": sample_type,
                    "notes": f"Enrolado desde agente de cámara ({sample_type})",
                },
                timeout=4.0,
            )
            if status_code == 201 and isinstance(data, dict):
                print(f"[OK] {data.get('message', 'Registrado correctamente')}")
                return
            elif status_code > 0:
                print(f"[!] Error API ({status_code}): {data}")
            else:
                print(f"[!] API fuera de línea o sin respuesta, usando guardado directo en DB...")

            # Fallback to direct DB
            from app.db.database import SessionLocal
            from app.models import Employee, EmployeeBiometric
            import json

            db = SessionLocal()
            try:
                emp = db.get(Employee, emp_id)
                if not emp:
                    print(f"[ERROR] Empleado #{emp_id} no existe en la base de datos.")
                    return

                faces, low_light, _ = self.pipeline.detect_faces(frame)
                if faces is None or len(faces) == 0:
                    print("[ERROR] No se detectó rostro ni ojos en el encuadre.")
                    return

                best_face = max(faces, key=lambda f: f[14])
                feats = self.pipeline.extract_features(frame, best_face)

                bio = EmployeeBiometric(
                    employee_id=emp.id,
                    sample_type=sample_type,
                    full_face_vector=json.dumps(feats["full_face_vector"]),
                    periocular_vector=json.dumps(feats["periocular_vector"]),
                    notes=f"Registro directo cámara ({sample_type}, máscara={feats['is_masked']})",
                    is_active=True,
                )
                db.add(bio)
                db.commit()
                print(f"[OK] Biometría guardada para {emp.firstName} {emp.lastName} (ID {emp.id}).")
            finally:
                db.close()

        except Exception as err:
            print(f"[ERROR] Error al registrar: {err}")
        print("=" * 50 + "\n")

    def run(self):
        print("\n=======================================================")
        print("   CleanSpace - Agente de Visión y Control de Acceso   ")
        print("       Biometría Periocular para Cuartos Limpios      ")
        print("=======================================================")
        print(f" * Backend API: {self.api_url}")
        print(f" * Puerta asignada: {self.door_id}")
        print(" * Controles:")
        print("   [ESPACIO] : Verificar acceso ahora")
        print("   [R]       : Registrar rostro/ojos de empleado")
        print("   [L]       : Alternar luz de pantalla (soft-box)")
        print("   [A]       : Alternar auto-verificación continua")
        print("   [Q]       : Salir")
        print("=======================================================\n")

        cap = cv2.VideoCapture(self.cam_idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.cam_idx)

        if not cap.isOpened():
            print(f"[ERROR] No se pudo abrir la cámara índice {self.cam_idx}.")
            return

        # Request 640x480 resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        window_name = "CleanSpace AI - Control de Acceso Cuarto Limpio"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 900, 680)

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    print("[!] Esperando señal de cámara...")
                    time.sleep(0.1)
                    continue

                display_frame = frame.copy()
                h, w = frame.shape[:2]

                # Low light detection & enhancement
                enhanced_frame, low_light_active, l_mean = self.pipeline.enhance_low_light(frame)

                # Face and eye detection
                faces, _, _ = self.pipeline.detect_faces(frame)

                now = time.time()
                has_face = faces is not None and len(faces) > 0

                if not has_face:
                    # Clear result immediately if person leaves the frame
                    self.last_result = None

                # Auto-verification when face is stable
                if has_face and self.auto_verify and (now - self.last_verify_time > self.verify_cooldown):
                    self.last_verify_time = now
                    self.last_result = self.call_verify_api(frame)
                    self.last_result_expiry = now + 2.5  # Keep HUD result for 2.5 seconds

                # Draw detections & HUD
                if has_face:
                    best_face = max(faces, key=lambda f: f[14])
                    fx, fy, fw, fh = int(best_face[0]), int(best_face[1]), int(best_face[2]), int(best_face[3])

                    # Eye coordinates
                    rx, ry = int(best_face[4]), int(best_face[5])
                    lx, ly = int(best_face[6]), int(best_face[7])

                    # Default color: yellow (evaluating)
                    box_color = (0, 215, 255)
                    status_text = "ANALIZANDO..."

                    if self.last_result and now < self.last_result_expiry:
                        if self.last_result.get("authorized"):
                            box_color = (0, 255, 0)  # Green
                            status_text = "ACCESO AUTORIZADO"
                        else:
                            box_color = (0, 0, 255)  # Red
                            status_text = "ACCESO DENEGADO"

                    # Face bounding box
                    cv2.rectangle(display_frame, (fx, fy), (fx + fw, fy + fh), box_color, 2)

                    # Draw Periocular Region (Eyes + Eyebrows) in Cyan
                    dx = lx - rx
                    dy = ly - ry
                    dist = math.sqrt(dx * dx + dy * dy)
                    if dist > 5:
                        cx, cy = int((rx + lx) / 2), int((ry + ly) / 2)
                        p_w = int(dist * 2.4)
                        p_h = int(dist * 1.2)
                        px1 = max(0, int(cx - p_w / 2))
                        py1 = max(0, int(cy - p_h * 0.65))
                        px2 = min(w, px1 + p_w)
                        py2 = min(h, py1 + p_h)
                        cv2.rectangle(display_frame, (px1, py1), (px2, py2), (255, 255, 0), 1)
                        cv2.putText(
                            display_frame,
                            "ZONA PERIOCULAR (OJOS)",
                            (px1, max(15, py1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.4,
                            (255, 255, 0),
                            1,
                        )

                    # Draw eye landmarks
                    cv2.circle(display_frame, (rx, ry), 4, (0, 255, 255), -1)
                    cv2.circle(display_frame, (lx, ly), 4, (0, 255, 255), -1)

                # Top Status Bar (Cleanroom & Lighting status)
                top_overlay = display_frame.copy()
                cv2.rectangle(top_overlay, (0, 0), (w, 55), (20, 20, 20), -1)
                cv2.addWeighted(top_overlay, 0.75, display_frame, 0.25, 0, display_frame)

                cv2.putText(
                    display_frame,
                    f"CleanSpace AI - Puerta: {self.door_id}",
                    (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 255, 255),
                    2,
                )

                # Light indicator
                if low_light_active:
                    light_label = f"VISIÓN NOCTURNA ACTIVA (L={int(l_mean)})"
                    light_color = (0, 165, 255)  # Orange
                else:
                    light_label = f"ILUMINACIÓN NORMAL (L={int(l_mean)})"
                    light_color = (0, 255, 128)  # Light Green

                cv2.putText(
                    display_frame,
                    light_label,
                    (10, 44),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    light_color,
                    1,
                )

                # Result HUD Banner (Bottom)
                if self.last_result and now < self.last_result_expiry:
                    auth = self.last_result.get("authorized", False)
                    banner_color = (0, 140, 0) if auth else (0, 0, 160)
                    bot_overlay = display_frame.copy()
                    cv2.rectangle(bot_overlay, (0, h - 90), (w, h), banner_color, -1)
                    cv2.addWeighted(bot_overlay, 0.8, display_frame, 0.2, 0, display_frame)

                    if auth:
                        emp_name = self.last_result.get("employee_name", "Desconocido")
                        dept = self.last_result.get("department", "Cuarto Limpio")
                        score = int(self.last_result.get("confidence_score", 0.0) * 100)
                        mode = self.last_result.get("match_mode", "PERIOCULAR_EYES")
                        mode_str = "OJOS / CUBREBOCAS" if mode == "PERIOCULAR_EYES" else "ROSTRO COMPLETO"

                        cv2.putText(
                            display_frame,
                            f"ACCESO CONCEDIDO: {emp_name} ({dept})",
                            (15, h - 60),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.65,
                            (255, 255, 255),
                            2,
                        )
                        cv2.putText(
                            display_frame,
                            f"Modo: {mode_str} | Similitud: {score}% | Puerta {self.door_id} DESBLOQUEADA [5s]",
                            (15, h - 25),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.48,
                            (200, 255, 200),
                            1,
                        )
                    else:
                        reason = self.last_result.get("reason", "unknown_face")
                        reason_msg = "Rostro / Ojos no registrados" if reason == "unknown_face" else reason
                        cv2.putText(
                            display_frame,
                            "ACCESO DENEGADO - NO AUTORIZADO",
                            (15, h - 55),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.65,
                            (255, 255, 255),
                            2,
                        )
                        cv2.putText(
                            display_frame,
                            f"Motivo: {reason_msg} | Alerta registrada en CleanSpace API",
                            (15, h - 25),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.48,
                            (200, 200, 255),
                            1,
                        )

                # Screen light simulation (Bright border around window)
                if self.screen_light_enabled:
                    border_thickness = 35
                    cv2.rectangle(
                        display_frame,
                        (0, 0),
                        (w, h),
                        (255, 255, 255),
                        border_thickness,
                    )
                    cv2.putText(
                        display_frame,
                        "SOFT-BOX ACTIVO (LUZ MONITOR)",
                        (w - 280, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        (50, 50, 255),
                        1,
                    )

                cv2.imshow(window_name, display_frame)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q") or key == 27:  # Q or ESC
                    break
                elif key == 32:  # Space
                    print("[*] Verificación manual disparada...")
                    self.last_verify_time = now
                    self.last_result = self.call_verify_api(frame)
                    self.last_result_expiry = now + 5.0
                    print(f" -> Resultado: {self.last_result}")
                elif key == ord("r") or key == ord("R"):
                    self.enroll_face_interactively(frame)
                elif key == ord("l") or key == ord("L"):
                    self.toggle_screen_light()
                elif key == ord("a") or key == ord("A"):
                    self.auto_verify = not self.auto_verify
                    print(f"[*] Auto-verificación continua: {self.auto_verify}")

        finally:
            cap.release()
            cv2.destroyAllWindows()
            print("[*] Cámara y agente detenidos correctamente.")


if __name__ == "__main__":
    agent = FacialCameraAgent()
    agent.run()
