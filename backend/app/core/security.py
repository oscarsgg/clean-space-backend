from datetime import datetime, timedelta, timezone
import hashlib, hmac, secrets
from jose import JWTError, jwt
from pwdlib import PasswordHash
from .config import get_settings

password_hash = PasswordHash.recommended()

def hash_password(password: str) -> str: return password_hash.hash(password)
def verify_password(password: str, hashed: str) -> bool: return password_hash.verify(password, hashed)
def hash_refresh(token: str) -> str: return hashlib.sha256(token.encode()).hexdigest()
def hash_rfid(uid: str) -> str:
    return hmac.new(get_settings().rfid_hmac_key.encode(), uid.strip().upper().encode(), hashlib.sha256).hexdigest()
def create_token(subject: str, token_type: str, expires: timedelta, roles: list[str] = []):
    settings = get_settings(); now = datetime.now(timezone.utc)
    claims = {"sub": subject, "type": token_type, "iat": now, "exp": now + expires, "iss": settings.jwt_issuer, "aud": settings.jwt_audience, "jti": secrets.token_hex(16)}
    if roles: claims["roles"] = roles
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
def decode_token(token: str, expected_type: str):
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm], issuer=settings.jwt_issuer, audience=settings.jwt_audience)
        if payload.get("type") != expected_type or not payload.get("sub"): return None
        return payload
    except JWTError: return None
