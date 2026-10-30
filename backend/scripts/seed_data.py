from sqlalchemy import select
from app.core.config import get_settings
from app.core.security import hash_password, hash_rfid
from app.db.session import Base, engine, SessionLocal
from app.models.models import *
Base.metadata.create_all(bind=engine)
db=SessionLocal(); s=get_settings()
roles={"ADMINISTRADOR":"Administrador","SUPERVISOR":"Supervisor","PERSONAL_AUTORIZADO":"Personal autorizado"}
for code,name in roles.items():
    if not db.scalar(select(Role).where(Role.code==code)): db.add(Role(code=code,name=name))
db.commit()
def user(email,password,name,role):
    u=db.scalar(select(User).where(User.email==email))
    if not u:
        u=User(email=email,password_hash=hash_password(password),full_name=name); u.roles=[db.scalar(select(Role).where(Role.code==role))]; db.add(u); db.commit(); db.refresh(u)
    return u
admin=user(s.initial_admin_email,s.initial_admin_password,"CleanSpace Administrator","ADMINISTRADOR")
sup=user("supervisor@cleanspace.local","Supervisor_123!","Supervisión CleanSpace","SUPERVISOR")
p=user("personal@cleanspace.local","Personal_123!","Personal autorizado","PERSONAL_AUTORIZADO")
for code,name in [("SALA-A","Laboratorio principal"),("SALA-B","Zona de preparación")]:
    if not db.scalar(select(Cleanroom).where(Cleanroom.code==code)): db.add(Cleanroom(code=code,name=name,location="Edificio académico"))
db.commit(); rooms=db.scalars(select(Cleanroom)).all()
for i,r in enumerate(rooms,1):
    if not db.scalar(select(Device).where(Device.code==f"DEV-{i}")): db.add(Device(cleanroom_id=r.id,code=f"DEV-{i}",type="SENSOR",serial_number=f"SIM-{i}"))
    if not db.scalar(select(MonitoringParameter).where(MonitoringParameter.cleanroom_id==r.id,MonitoringParameter.metric=="temperature")): db.add(MonitoringParameter(cleanroom_id=r.id,metric="temperature",min_value=18,max_value=24,unit="°C"))
if not db.scalar(select(RfidCard).where(RfidCard.user_id==p.id)): db.add(RfidCard(user_id=p.id,uid_hash=hash_rfid("04-A1-B2-C3")))
db.commit(); print("Seed completado. No se muestran contraseñas.")
