from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models import AccessLog
from app.schemas.access_logs import AccessLogCreate, AccessLogRead


router = APIRouter(prefix="/access-logs", tags=["Access logs"])


@router.post("/", response_model=AccessLogRead, status_code=201)
def create_access_log(log_data: AccessLogCreate, session: Session = Depends(get_db)):
    access_log = AccessLog(**log_data.model_dump())
    session.add(access_log)
    session.commit()
    session.refresh(access_log)
    return access_log


@router.get("/", response_model=list[AccessLogRead])
def get_access_logs(limit: int = 100, session: Session = Depends(get_db)):
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit debe estar entre 1 y 500")
    statement = select(AccessLog).order_by(desc(AccessLog.id)).limit(limit)
    return session.scalars(statement).all()