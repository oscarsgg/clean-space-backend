# Sistema de Reconocimiento Facial y Periocular para Cuarto Limpio (CleanSpace & ESP-Claw)

Este módulo implementa el subsistema de **control de acceso biométrico de alta robustez** diseñado específicamente para las condiciones críticas de un **Cuarto Limpio (Clean Room / Sala Blanca ISO 5 - ISO 8)** y su integración con el framework de agentes **ESP-Claw** y la API **CleanSpace**.

---

## 🔬 Desafíos Resueltos

1. **Equipo de Protección Personal (EPP / Cubrebocas / Cofia / Escafandra)**:
   - En áreas limpias farmacéuticas o de semiconductores, el personal utiliza mascarillas quirúrgicas/N95 y cofias o escafandras, cubriendo boca, nariz, barbilla y orejas.
   - **Solución implementada**: **Biometría Periocular**. El sistema detecta la cara y aísla la **zona ocular (ojos, cejas, pliegues palpebrales y puente nasal superior)** mediante redes profundas YuNet y alineación métrica SFace. Permite identificar al personal aun cuando el 70% del rostro esté cubierto.

2. **Condiciones Extremas de Luz (Oscuridad total o poca luz)**:
   - Durante ciclos de esterilización UV, guardias nocturnas o esclusas de aire (airlocks) en penumbra.
   - **Solución implementada**: Pipeline adaptativo de visión nocturna en espacio de color LAB:
     - **Corrección Gamma dinámica** según luminancia media: $\gamma = \frac{\ln(0.5)}{\ln(L_{mean}/255)}$.
     - **CLAHE (Contrast Limited Adaptive Histogram Equalization)** en el canal de luminancia $L$ con límite de recorte $3.5$.
     - **Filtro bilateral** que atenúa el ruido de sensor sin degradar los bordes de los ojos.
     - **Modo Soft-box (Luz de monitor)**: marco blanco de alta luminosidad que ilumina físicamente el rostro del operador en oscuridad absoluta.

3. **Integración con ESP-Claw y Hardware ESP32**:
   - Vinculación directa con los modelos de `clean-space-backend` (`Employee`, `DoorPermission`, `AccessLog`, `Command`).
   - Envío de comando `open_door` al relé del ESP32 cuando el acceso es autorizado.
   - Skill nativo de ESP-Claw en `components/claw_capabilities/cap_mcp_client/skills/control_acceso_cuarto_limpio/SKILL.md`.
   - Regla en `router_rules.json` para disparar la verificación ante eventos del cuarto limpio.

---

## 🛠️ Arquitectura de Archivos Creados

| Componente | Archivo | Propósito |
|---|---|---|
| **Modelo BD** | `app/models/biometric.py` | Tabla `employee_biometrics` con vectores de 128 dimensiones (rostro completo y región periocular). |
| **Núcleo de Visión** | `app/core/face_biometrics.py` | Pipeline con modelos ONNX YuNet + SFace, realce nocturno, detección de cubrebocas y similitud coseno. |
| **Modelos ONNX** | `app/vision_models/` | Pesos oficiales de YuNet (detector facial ultrarrápido) y SFace (extractor de características faciales). |
| **Esquemas Pydantic** | `app/schemas/facial.py` | DTOs para `FacialEnrollRequest`, `FacialVerifyRequest`, etc. |
| **API Endpoints** | `app/api/v1/facial_access.py` | Endpoints REST `/api/v1/facial/enroll`, `/verify`, `/biometrics/{id}`. |
| **Agente de Cámara** | `scripts/facial_camera_agent.py` | Aplicación interactiva de cámara web con HUD en tiempo real, detección de EPP y registro biométrico. |
| **Seed Empleados** | `scripts/seed_cleanroom_employees.py` | Inicialización de empleados de cuarto limpio y permisos de puerta `entrada-principal`. |
| **Pruebas Unitarias** | `tests/test_facial_access.py` | Suite de pruebas automatizadas con pytest para el pipeline y endpoints. |
| **Skill ESP-Claw** | `esp-claw/.../control_acceso_cuarto_limpio/` | Definición de Skill para el agente IoT ESP-Claw. |

---

## 🚀 Guía de Uso Paso a Paso

### 1. Inicializar la base de datos y empleados
En una terminal PowerShell con el entorno virtual activo:
```powershell
cd C:\Users\rosal\claseMike\ProyectoSpaceClean\clean-space-backend\backend
..\..\.venv\Scripts\activate
python scripts/seed_cleanroom_employees.py
```

### 2. Iniciar la API FastAPI CleanSpace
```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
*Documentación interactiva disponible en: `http://127.0.0.1:8000/docs`.*

### 3. Ejecutar el Agente de Visión por Cámara Web
En otra terminal:
```powershell
cd C:\Users\rosal\claseMike\ProyectoSpaceClean\clean-space-backend\backend
..\..\.venv\Scripts\activate
python scripts/facial_camera_agent.py
```

#### Controles del Agente de Cámara:
- **`[R]`**: **Registrar Rostro / Enrolar**:
  - Solicita en consola el ID del empleado (ej. `1` para Carlos Méndez).
  - Permite elegir si se registra con **Rostro Completo** o con **Cuarto Limpio (Cubrebocas/Cofia)**.
  - Guarda automáticamente los vectores en la base de datos de CleanSpace.
- **`[ESPACIO]`**: Disparar verificación manual instantánea.
- **`[L]`**: Activar / desactivar marco de luz de monitor (Soft-box para cuartos a oscuras).
- **`[A]`**: Alternar auto-verificación continua cada 2 segundos.
- **`[Q]` o `[ESC]`**: Cerrar agente y liberar cámara.

---

## 🦞 Interacción desde ESP-Claw

Cuando el agente ESP-Claw recibe una orden por Telegram, chat o sensor de puerta (por ejemplo: *"Verifica quién está en la puerta del cuarto limpio"*), el agente ejecuta el flujo:
1. Llama a `http://<IP_LOCAL>:8000/api/v1/facial/verify`.
2. Si `authorized: true`:
   - Activa el actuador/relé del cuarto limpio en el pin GPIO configurado.
   - Responde: *"Acceso Autorizado para Carlos Mendez (Operaciones Cuarto Limpio). Modo de identificación: OJOS / CUBREBOCAS. Puerta desbloqueada por 5 segundos."*
3. Si `authorized: false`:
   - Mantiene la puerta bloqueada y genera la alerta en el registro de accesos.
