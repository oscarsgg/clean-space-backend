from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings


def setup_cors(app, origins: list[str] | None = None):
    allowed_origins = get_settings().cors_origins if origins is None else origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )