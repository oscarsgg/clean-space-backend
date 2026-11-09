from fastapi import FastAPI

from app import models
from app.api.v1.router import router as v1_router
from app.core.cors import setup_cors
from app.core.config import get_settings
from app.db.database import Base, engine


settings = get_settings()
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name, version="2.0.0", description="API REST e IoT de CleanSpace")
setup_cors(app)
app.include_router(v1_router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name, "version": "2.0.0"}