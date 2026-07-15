"""Helpers for talking to the upstream OpenAI-compatible endpoint."""

from urllib.parse import urljoin

import httpx
from fastapi import Request

from backend.auth import bearer_token, is_backend_api_key
from backend.config import DEFAULT_API_KEY, normalize_endpoint_url


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


# Test seam: tests monkeypatch this with an httpx.MockTransport to fake the
# upstream. None means httpx uses its default transport.
upstream_transport: httpx.AsyncBaseTransport | None = None


def get_upstream_transport() -> httpx.AsyncBaseTransport | None:
    return upstream_transport


async def detect_proxy_models(base_url: str, api_key: str) -> list[str]:
    models_url = urljoin(f"{normalize_endpoint_url(base_url).rstrip('/')}/", "v1/models")
    async with httpx.AsyncClient(timeout=5, transport=get_upstream_transport()) as client:
        response = await client.get(
            models_url,
            headers={"Authorization": f"Bearer {api_key.strip() or DEFAULT_API_KEY}"},
        )
        response.raise_for_status()

    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []

    models: list[str] = []
    for item in data:
        if isinstance(item, dict):
            model_id = str(item.get("id", "")).strip()
            if model_id:
                models.append(model_id)
    return models


def upstream_headers(request: Request, api_key: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    incoming_auth = ""
    for name, value in request.headers.items():
        lowered = name.lower()
        if lowered in HOP_BY_HOP_HEADERS or lowered in {"host", "content-length"}:
            continue
        if lowered == "authorization":
            incoming_auth = value
            continue
        headers[name] = value

    # Never forward the dashboard key upstream; substitute the configured
    # upstream key when the caller authenticated with it.
    if incoming_auth and is_backend_api_key(bearer_token(incoming_auth)):
        incoming_auth = ""
    headers["Authorization"] = incoming_auth or f"Bearer {api_key}"
    return headers


def response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {name: value for name, value in headers.items() if name.lower() not in HOP_BY_HOP_HEADERS}


def proxy_upstream_url(base_url: str, path: str) -> str:
    return urljoin(f"{normalize_endpoint_url(base_url).rstrip('/')}/", f"v1/{path}")
