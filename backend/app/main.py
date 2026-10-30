from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Depends, HTTPException, Request, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.security import *
from app.db.session import Base, engine, get_db
from app.models.models import *

settings=get_settings(); Base.metadata.create_all(bind=engine)
app=FastAPI(title=settings.app_name, version="1.0.0", description="API REST de CleanSpace")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
oauth2=OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
class LoginIn(BaseModel): email: EmailStr; password: str
class RefreshIn(BaseModel): refresh_token: str
class TokenOut(BaseModel): access_token:str; refresh_token:str; token_type:str; expires_in:int; user:dict
class PersonIn(BaseModel): email: EmailStr; password: str = Field(min_length=12); full_name:str; role_codes:list[str]=["PERSONAL_AUTORIZADO"]
class RoomIn(BaseModel): code:str; name:str; location:str=""; description:str=""; access_level:str="STANDARD"
class DeviceIn(BaseModel): cleanroom_id:int; code:str; type:str; serial_number:str
class ReadingIn(BaseModel): device_id:int; cleanroom_id:int; metric:str; value:float; unit:str

def current_user(token:str=Depends(oauth2), db:Session=Depends(get_db)):
    payload=decode_token(token,"access"); user=db.get(User,int(payload["sub"])) if payload else None
    if not user or user.status!="ACTIVE": raise HTTPException(401,"Credenciales inválidas")
    return user
def require(*roles):
    def dep(user=Depends(current_user)):
        if not any(r.code in roles for r in user.roles): raise HTTPException(403,"Permisos insuficientes")
        return user
    return dep
def user_out(u): return {"id":u.id,"email":u.email,"full_name":u.full_name,"roles":[r.code for r in u.roles]}
def issue_tokens(db,u):
    access=create_token(str(u.id),"access",timedelta(minutes=settings.access_token_expire_minutes),[r.code for r in u.roles]); refresh=secrets.token_urlsafe(48)
    db.add(RefreshSession(user_id=u.id, token_hash=hash_refresh(refresh), expires_at=datetime.now(timezone.utc)+timedelta(days=settings.refresh_token_expire_days))); db.commit()
    return TokenOut(access_token=access,refresh_token=refresh,token_type="bearer",expires_in=settings.access_token_expire_minutes*60,user=user_out(u))
@app.get("/health")
def health(): return {"status":"ok","service":settings.app_name}
@app.post("/api/v1/auth/login",response_model=TokenOut)
async def login(request: Request, db:Session=Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        email = payload.get("email")
        password = payload.get("password")
    else:
        form_data = await request.form()
        email = form_data.get("username")
        password = form_data.get("password")
    if not email or not password:
        raise HTTPException(422, "Faltan email o password")
    email = str(email).lower().strip()
    u=db.scalar(select(User).where(User.email==email))
    if not u or not verify_password(password,u.password_hash) or u.status!="ACTIVE": raise HTTPException(401,"Credenciales inválidas")
    return issue_tokens(db,u)
@app.post("/api/v1/auth/refresh",response_model=TokenOut)
def refresh(data:RefreshIn,db:Session=Depends(get_db)):
    s=db.scalar(select(RefreshSession).where(RefreshSession.token_hash==hash_refresh(data.refresh_token)))
    if not s or s.revoked_at or s.expires_at<datetime.now(timezone.utc): raise HTTPException(401,"Refresh token inválido")
    s.revoked_at=datetime.now(timezone.utc); u=db.get(User,s.user_id); db.commit(); return issue_tokens(db,u)
@app.post("/api/v1/auth/logout",status_code=204)
def logout(data:RefreshIn,db:Session=Depends(get_db),user=Depends(current_user)):
    s=db.scalar(select(RefreshSession).where(RefreshSession.token_hash==hash_refresh(data.refresh_token),RefreshSession.user_id==user.id));
    if s: s.revoked_at=datetime.now(timezone.utc); db.commit()
@app.get("/api/v1/auth/me")
def me(user=Depends(current_user)): return user_out(user)
@app.get("/api/v1/roles")
def roles(user=Depends(require("ADMINISTRADOR")),db:Session=Depends(get_db)): return [{"id":r.id,"code":r.code,"name":r.name} for r in db.scalars(select(Role)).all()]
@app.get("/api/v1/personas")
def people(skip:int=0,limit:int=50,status_filter:str|None=None,db:Session=Depends(get_db),user=Depends(require("ADMINISTRADOR","SUPERVISOR"))):
    q=select(User).offset(skip).limit(min(limit,100));
    if status_filter: q=q.where(User.status==status_filter)
    return [user_out(u) for u in db.scalars(q).all()]
@app.post("/api/v1/personas",status_code=201)
def create_person(data:PersonIn,db:Session=Depends(get_db),user=Depends(require("ADMINISTRADOR"))):
    if db.scalar(select(User).where(User.email==data.email.lower())): raise HTTPException(409,"El correo ya existe")
    u=User(email=data.email.lower(),password_hash=hash_password(data.password),full_name=data.full_name); u.roles=list(db.scalars(select(Role).where(Role.code.in_(data.role_codes))).all()); db.add(u); db.commit(); db.refresh(u); return user_out(u)
@app.patch("/api/v1/personas/{id}")
def deactivate_person(id:int,db:Session=Depends(get_db),user=Depends(require("ADMINISTRADOR"))):
    u=db.get(User,id)
    if not u: raise HTTPException(404,"Persona no encontrada")
    u.status="INACTIVE"; db.commit(); return user_out(u)
@app.get("/api/v1/cuartos")
def rooms(db:Session=Depends(get_db),user=Depends(current_user)): return [{"id":r.id,"code":r.code,"name":r.name,"status":r.status,"location":r.location} for r in db.scalars(select(Cleanroom)).all()]
@app.post("/api/v1/cuartos",status_code=201)
def create_room(data:RoomIn,db:Session=Depends(get_db),user=Depends(require("ADMINISTRADOR"))):
    if db.scalar(select(Cleanroom).where(Cleanroom.code==data.code)): raise HTTPException(409,"Código duplicado")
    r=Cleanroom(**data.model_dump()); db.add(r); db.commit(); db.refresh(r); return r
@app.patch("/api/v1/cuartos/{id}")
def update_room(id:int,status_value:str|None=None,db:Session=Depends(get_db),user=Depends(require("ADMINISTRADOR"))):
    r=db.get(Cleanroom,id)
    if not r: raise HTTPException(404,"Cuarto no encontrado")
    if status_value: r.status=status_value
    db.commit(); return r
@app.get("/api/v1/dispositivos")
def devices(db:Session=Depends(get_db),user=Depends(current_user)): return db.scalars(select(Device)).all()
@app.post("/api/v1/dispositivos",status_code=201)
def create_device(data:DeviceIn,db:Session=Depends(get_db),user=Depends(require("ADMINISTRADOR"))):
    if not db.get(Cleanroom,data.cleanroom_id): raise HTTPException(400,"Cuarto inválido")
    d=Device(**data.model_dump()); db.add(d); db.commit(); db.refresh(d); return d
@app.post("/api/v1/monitoreo/lecturas",status_code=201)
def add_reading(data:ReadingIn,db:Session=Depends(get_db),user=Depends(require("ADMINISTRADOR","SUPERVISOR"))):
    if not db.get(Device,data.device_id) or not db.get(Cleanroom,data.cleanroom_id): raise HTTPException(400,"Dispositivo o cuarto inválido")
    reading=SensorReading(**data.model_dump()); db.add(reading); p=db.scalar(select(MonitoringParameter).where(MonitoringParameter.cleanroom_id==data.cleanroom_id,MonitoringParameter.metric==data.metric,MonitoringParameter.enabled==True))
    db.commit(); db.refresh(reading)
    if p and (data.value<p.min_value or data.value>p.max_value): db.add(Alert(cleanroom_id=data.cleanroom_id,device_id=data.device_id,reading_id=reading.id,type="OUT_OF_RANGE",severity="HIGH",message=f"{data.metric} fuera de rango")); db.commit()
    return reading
@app.get("/api/v1/monitoreo/historico")
def readings(db:Session=Depends(get_db),user=Depends(current_user)): return db.scalars(select(SensorReading).order_by(SensorReading.recorded_at.desc()).limit(200)).all()
@app.get("/api/v1/avisos")
def alerts(db:Session=Depends(get_db),user=Depends(current_user)): return db.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(100)).all()
@app.patch("/api/v1/avisos/{id}/acknowledge")
def acknowledge(id:int,db:Session=Depends(get_db),user=Depends(require("ADMINISTRADOR","SUPERVISOR"))):
    a=db.get(Alert,id)
    if not a: raise HTTPException(404,"Aviso no encontrado")
    a.status="ACKNOWLEDGED"; a.acknowledged_by=user.id; a.acknowledged_at=datetime.now(timezone.utc); db.commit(); return a
@app.get("/api/v1/dashboard/admin/summary")
def summary(db:Session=Depends(get_db),user=Depends(require("ADMINISTRADOR"))): return {"cleanrooms":db.scalar(select(func.count(Cleanroom.id))),"active_people":db.scalar(select(func.count(User.id)).where(User.status=="ACTIVE")),"devices":db.scalar(select(func.count(Device.id))),"readings":db.scalar(select(func.count(SensorReading.id))),"active_alerts":db.scalar(select(func.count(Alert.id)).where(Alert.status=="ACTIVE"))}
