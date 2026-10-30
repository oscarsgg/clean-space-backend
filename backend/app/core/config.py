import json
from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "CleanSpace API"
    app_env: str = "development"
    app_debug: bool = False
    database_url: str = "sqlite:///./cleanspace.db"
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "cleanspace-api"
    jwt_audience: str = "cleanspace-frontend"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    rfid_hmac_key: str
    cors_origins: list[str] | str = ["http://localhost:3000"]
    initial_admin_email: str = "admin@cleanspace.local"
    initial_admin_password: str
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            return []
        if value.startswith("["):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in value.split(",") if item.strip()]

    @field_validator("jwt_secret_key", "rfid_hmac_key", "initial_admin_password")
    @classmethod
    def reject_defaults(cls, value):
        if len(value) < 12: raise ValueError("La configuración de seguridad debe tener al menos 12 caracteres")
        return value

@lru_cache
def get_settings(): return Settings()
