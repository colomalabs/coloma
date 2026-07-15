"""Dashboard API-key authentication for all backend endpoints."""

import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.config import APP_DIR


ENV_PATH = APP_DIR / ".env"
API_KEY_ENV_VAR = "COLOMA_API_KEY"
API_KEY_HEADER = "x-api-key"


def read_backend_api_key() -> str:
    env_value = os.environ.get(API_KEY_ENV_VAR)
    if env_value is not None:
        return env_value.strip()
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            if name.strip() == API_KEY_ENV_VAR:
                return value.strip().strip("\"'")
    return ""


BACKEND_API_KEY = read_backend_api_key()


def bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def is_backend_api_key(token: str) -> bool:
    return bool(BACKEND_API_KEY) and secrets.compare_digest(token.encode(), BACKEND_API_KEY.encode())


async def require_api_key(request: Request, call_next):
    if BACKEND_API_KEY and request.method != "OPTIONS":
        provided = request.headers.get(API_KEY_HEADER, "") or bearer_token(request.headers.get("authorization", ""))
        if not is_backend_api_key(provided):
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
    return await call_next(request)
