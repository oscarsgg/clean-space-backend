from fastapi import APIRouter

from app.api.v1.access import router as access_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.devices import router as devices_router
from app.api.v1.device_access import router as device_access_router
from app.api.v1.employees import router as employees_router
from app.api.v1.access_logs import router as access_logs_router
from app.api.v1.door_permissions import router as door_permissions_router
from app.api.v1.iot import router as iot_router
from app.api.v1.monitoring import router as monitoring_router
from app.api.v1.people import router as people_router
from app.api.v1.rfid_tags import router as rfid_tags_router
from app.api.v1.rooms import router as rooms_router


router = APIRouter()
router.include_router(auth_router)
router.include_router(people_router)
router.include_router(rooms_router)
router.include_router(devices_router)
router.include_router(access_router)
router.include_router(iot_router)
router.include_router(monitoring_router)
router.include_router(analytics_router)
router.include_router(employees_router)
router.include_router(rfid_tags_router)
router.include_router(access_logs_router)
router.include_router(door_permissions_router)
router.include_router(device_access_router)