import asyncio

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core.security import decode_token
from app.db.database import SessionLocal
from app.models import User
from app.services.realtime import realtime_broker


router = APIRouter(tags=["Realtime"])


def _extract_token(authorization: str | None, token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    if token:
        return token.strip()
    return None


@router.get("/eventos/stream")
async def stream_events(
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    raw_token = _extract_token(authorization, token)
    if not raw_token:
        raise HTTPException(401, "Token de acceso requerido")
    payload = decode_token(raw_token, "access")
    with SessionLocal() as db:
        user = db.get(User, int(payload["sub"])) if payload else None
        is_valid = bool(user and user.status == "ACTIVE")
    if not is_valid:
        raise HTTPException(401, "Credenciales inválidas")

    async def event_generator():
        yield 'event: connected\ndata: {"status":"ok"}\n\n'
        async with realtime_broker.subscription() as queue:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield frame
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
