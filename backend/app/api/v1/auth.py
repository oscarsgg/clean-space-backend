from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import current_user, issue_tokens, settings, utcnow, user_out
from app.core.security import hash_refresh, verify_password
from app.db.database import get_db
from app.models import RefreshSession, User
from app.schemas.auth import LoginIn, RefreshIn


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        email, password = data.get("email"), data.get("password")
    else:
        form = await request.form()
        email, password = form.get("username"), form.get("password")
    user = db.scalar(select(User).where(User.email == str(email).lower().strip())) if email else None
    if not user or not password or not verify_password(password, user.password_hash) or user.status != "ACTIVE":
        raise HTTPException(401, "Credenciales inválidas")
    return issue_tokens(db, user)


@router.post("/refresh")
def refresh(data: RefreshIn, response: Response, db: Session = Depends(get_db)):
    token = data.refresh_token
    session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == hash_refresh(token or "")))
    if not session or session.revoked_at or session.expires_at < utcnow():
        raise HTTPException(401, "Refresh token inválido")
    session.revoked_at = utcnow()
    user = db.get(User, session.user_id)
    db.commit()
    result = issue_tokens(db, user)
    response.set_cookie(
        "refresh_token",
        result["refresh_token"],
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 86400,
    )
    return result


@router.post("/logout", status_code=204)
def logout(
    data: RefreshIn,
    response: Response,
    db: Session = Depends(get_db),
    user=Depends(current_user),
):
    session = db.scalar(
        select(RefreshSession).where(
            RefreshSession.token_hash == hash_refresh(data.refresh_token or ""),
            RefreshSession.user_id == user.id,
        )
    )
    if session:
        session.revoked_at = utcnow()
        db.commit()
    response.delete_cookie("refresh_token")


@router.get("/me")
def me(user=Depends(current_user)):
    return user_out(user)