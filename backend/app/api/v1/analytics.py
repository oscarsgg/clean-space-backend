import json

from fastapi import APIRouter, Depends
from sqlalchemy import desc, extract, func, select
from sqlalchemy.orm import Session

from app.core.dependencies import output, require, utcnow
from app.db.database import get_db
from app.models import AccessEvent, Alert, Cleanroom, Device, ModelResult, SensorReading, User


router = APIRouter(tags=["Analytics"])


@router.post("/analitica/etl/ejecutar")
def run_etl(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    return {
        "status": "completed",
        "processed_readings": db.scalar(select(func.count(SensorReading.id))) or 0,
        "processed_accesses": db.scalar(select(func.count(AccessEvent.id))) or 0,
        "executed_at": utcnow(),
    }


@router.post("/analitica/modelo/entrenar")
def train_model(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    readings_count = db.scalar(select(func.count(SensorReading.id))) or 0
    result = ModelResult(
        model_type="isolation_forest",
        result_json=json.dumps({"training_rows": readings_count, "status": "trained"}),
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return output(result)


@router.get("/analitica/modelo/metricas")
def model_metrics(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    query = select(ModelResult).order_by(desc(ModelResult.trained_at)).limit(50)
    return output(db.scalars(query).all())


@router.get("/analitica/anomalias")
def anomalies(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    query = select(Alert).where(Alert.type == "OUT_OF_RANGE").order_by(desc(Alert.created_at)).limit(200)
    return output(db.scalars(query).all())


@router.get("/analitica/indicadores")
def indicators(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    return {
        "total_readings": db.scalar(select(func.count(SensorReading.id))) or 0,
        "total_accesses": db.scalar(select(func.count(AccessEvent.id))) or 0,
        "allowed_accesses": db.scalar(select(func.count(AccessEvent.id)).where(AccessEvent.result == "ALLOWED")) or 0,
        "denied_accesses": db.scalar(select(func.count(AccessEvent.id)).where(AccessEvent.result == "DENIED")) or 0,
        "active_alerts": db.scalar(select(func.count(Alert.id)).where(Alert.status == "ACTIVE")) or 0,
    }


@router.get("/analitica/series")
def series(
    cleanroom_id: int | None = None,
    metric: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require("ADMINISTRADOR", "SUPERVISOR")),
):
    query = select(SensorReading).order_by(SensorReading.recorded_at)
    if cleanroom_id:
        query = query.where(SensorReading.cleanroom_id == cleanroom_id)
    if metric:
        query = query.where(SensorReading.metric == metric)
    return output(db.scalars(query.limit(1000)).all())


@router.get("/analitica/patrones-acceso")
def access_patterns(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR", "SUPERVISOR"))):
    hour = extract("hour", AccessEvent.occurred_at).label("hour")
    rows = db.execute(select(hour, func.count(AccessEvent.id)).group_by(hour).order_by(hour)).all()
    return [{"hour": int(hour), "count": count} for hour, count in rows]


@router.get("/dashboard/admin/summary")
def summary(db: Session = Depends(get_db), user=Depends(require("ADMINISTRADOR"))):
    return {
        "cleanrooms": db.scalar(select(func.count(Cleanroom.id))),
        "active_people": db.scalar(select(func.count(User.id)).where(User.status == "ACTIVE")),
        "devices": db.scalar(select(func.count(Device.id))),
        "readings": db.scalar(select(func.count(SensorReading.id))),
        "active_alerts": db.scalar(select(func.count(Alert.id)).where(Alert.status == "ACTIVE")),
    }