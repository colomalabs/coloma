"""Dashboard API endpoints: config, status, models, and request history."""

import asyncio

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import DEFAULT_API_KEY, AppConfig, read_app_config, write_app_config
from backend import tee
from backend.tee import RequestSummary, list_active_request_summaries, list_saved_tee_records
from backend.upstream import detect_proxy_models
from backend.verification import (
    VerificationSummary,
    get_saved_verification,
    list_saved_verifications,
    summarize_verification,
    verification_jobs,
    verification_lock,
)


router = APIRouter()


class ConfigStatus(BaseModel):
    app_config: AppConfig


class ProxyTestRequest(BaseModel):
    base_url: str
    api_key: str = DEFAULT_API_KEY


class ProxyTestResult(BaseModel):
    ok: bool
    models: list[str] = []
    detail: str = ""
    error: str = ""


class UpstreamStatus(BaseModel):
    connected: bool
    model_count: int = 0
    detail: str = ""
    error: str = ""


class ModelsResponse(BaseModel):
    models: list[str] = []
    error: str = ""


class RequestsResponse(BaseModel):
    active: list[RequestSummary]
    saved: list[RequestSummary]


@router.get("/api/auth/check")
async def auth_check() -> dict[str, bool]:
    # Cheap probe for the frontend: the api-key middleware already ran.
    return {"ok": True}


@router.get("/api/config", response_model=ConfigStatus)
async def config_status() -> ConfigStatus:
    return ConfigStatus(app_config=read_app_config())


@router.put("/api/config", response_model=ConfigStatus)
async def update_config(config: AppConfig) -> ConfigStatus:
    return ConfigStatus(app_config=write_app_config(config))


async def try_detect_proxy_models(base_url: str, api_key: str) -> tuple[list[str], str]:
    """Detect upstream models, returning (models, error) instead of raising."""
    try:
        return await detect_proxy_models(base_url, api_key), ""
    except (httpx.HTTPError, ValueError) as exc:
        return [], str(exc)


@router.post("/api/config/test", response_model=ProxyTestResult)
async def test_proxy_config(payload: ProxyTestRequest) -> ProxyTestResult:
    models, error = await try_detect_proxy_models(payload.base_url, payload.api_key)
    if error:
        return ProxyTestResult(ok=False, error=error)
    return ProxyTestResult(ok=True, models=models, detail=f"Detected {len(models)} model(s)")


@router.get("/api/status", response_model=UpstreamStatus)
async def upstream_status() -> UpstreamStatus:
    config = read_app_config()
    models, error = await try_detect_proxy_models(config.proxy.base_url, config.proxy.api_key)
    if error:
        return UpstreamStatus(connected=False, error=error)
    return UpstreamStatus(
        connected=True, model_count=len(models), detail=f"Detected {len(models)} model(s)"
    )


@router.get("/api/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    config = read_app_config()
    models, error = await try_detect_proxy_models(config.proxy.base_url, config.proxy.api_key)
    return ModelsResponse(models=models, error=error)


def _attach_verification(summary: RequestSummary, verification: VerificationSummary | None) -> None:
    if verification is None:
        return
    summary.verification_status = verification.status
    summary.verification_resolved = verification.resolved
    summary.verification_response_body = verification.new_response_body
    summary.verification_issues = list(verification.new_issues)
    summary.verification_error = verification.error


@router.get("/api/requests", response_model=RequestsResponse)
async def request_history(limit: int = 100) -> RequestsResponse:
    """List request summaries without captured bodies; see request_detail for bodies."""
    config = read_app_config()
    bounded_limit = max(1, min(limit, 500))
    active, saved, verifications = await asyncio.gather(
        list_active_request_summaries(),
        asyncio.to_thread(list_saved_tee_records, config.proxy.db_path, bounded_limit),
        asyncio.to_thread(list_saved_verifications, config.proxy.db_path, bounded_limit),
    )
    verification_by_request: dict[str, VerificationSummary] = {}
    for verification in reversed(verifications):
        verification_by_request[verification.request_id] = verification
    async with verification_lock:
        for job in verification_jobs.values():
            verification_by_request[job.request_id] = summarize_verification(job, include_bodies=False)
    for summary in [*active, *saved]:
        _attach_verification(summary, verification_by_request.get(summary.request_id))
    return RequestsResponse(active=active, saved=saved)


@router.get("/api/requests/{request_id}", response_model=RequestSummary)
async def request_detail(request_id: str) -> RequestSummary:
    """Full record for one request, captured bodies included."""
    config = read_app_config()
    summary = await tee.get_active_request_summary(request_id)
    if summary is None:
        summary = await asyncio.to_thread(tee.get_saved_tee_record, config.proxy.db_path, request_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Request not found")

    async with verification_lock:
        job = next((job for job in verification_jobs.values() if job.request_id == request_id), None)
        verification = summarize_verification(job) if job is not None else None
    if verification is None:
        verification = await asyncio.to_thread(get_saved_verification, config.proxy.db_path, request_id)
    _attach_verification(summary, verification)
    return summary
