"""FastAPI app assembly: middleware, lifespan, and router wiring."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import auth
from backend.api import router as api_router
from backend.config import ConfigError
from backend.deploy import router as deploy_router
from backend.images import router as images_router
from backend.logger import logger
from backend.pressure import router as pressure_router
from backend.profiler import reap_orphaned_jobs, router as profiler_router
from backend.proxy import router as proxy_router
from backend.tasks import spawn_background_task
from backend.verification import reset_verification_queue, verification_worker


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if not auth.BACKEND_API_KEY:
        logger.warning(
            "%s is not set (backend/.env) — all endpoints are unauthenticated", auth.API_KEY_ENV_VAR,
        )
    await asyncio.to_thread(reap_orphaned_jobs)
    # Bind the verification queue to this event loop before the worker starts
    # consuming it (a previous loop may have owned it, e.g. across test apps).
    reset_verification_queue()
    worker = spawn_background_task(verification_worker(), "verification-worker")
    yield
    worker.cancel()


app = FastAPI(title="Coloma", lifespan=lifespan)


@app.exception_handler(ConfigError)
async def config_error_handler(_: Request, exc: ConfigError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=500)
app.include_router(profiler_router)
app.include_router(api_router)
app.include_router(deploy_router)
app.include_router(pressure_router)
app.include_router(images_router)
app.include_router(proxy_router)

app.middleware("http")(auth.require_api_key)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
