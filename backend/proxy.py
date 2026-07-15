"""The /v1 pass-through proxy: forwards requests upstream while teeing bodies."""

import time
import uuid
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from backend import tee
from backend.config import read_app_config
from backend.images import optimize_request_body
from backend.tasks import spawn_background_task
from backend.tee import TeeRecord, capture_body, extract_model
from backend.upstream import get_upstream_transport, proxy_upstream_url, response_headers, upstream_headers
from backend.validation import compute_validation_issues, extract_sse_content, synthesize_chat_completion_body
from backend.verification import enqueue_verification


router = APIRouter()


async def proxy_openai_request(path: str, request: Request) -> Response:
    config = read_app_config()
    proxy = config.proxy
    original_body = await request.body()
    request_body = optimize_request_body(original_body, config.optimization.max_mp)
    captured_original_body, original_truncated = capture_body(original_body, proxy.capture_bodies, proxy.max_body_bytes)
    captured_request_body, request_truncated = capture_body(request_body, proxy.capture_bodies, proxy.max_body_bytes)
    record = TeeRecord(
        request_id=str(uuid.uuid4()),
        started_at=time.time(),
        method=request.method,
        path=f"/v1/{path}",
        query_string=request.url.query,
        model=extract_model(original_body),
        request_body=captured_request_body,
        original_request_body=captured_original_body,
        request_truncated=request_truncated,
        original_request_truncated=original_truncated,
    )
    started = time.perf_counter()
    await tee.track_active_request(record)
    upstream_url = proxy_upstream_url(proxy.base_url, path)
    headers = upstream_headers(request, proxy.api_key)
    params = list(request.query_params.multi_items())

    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None), transport=get_upstream_transport())
    upstream_request = client.build_request(request.method, upstream_url, params=params, headers=headers, content=request_body)
    try:
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        record.latency_ms = (time.perf_counter() - started) * 1000
        record.error = str(exc)
        await tee.update_active_request(record)
        await tee.safe_write_tee_record(proxy.db_path, record)
        await tee.remove_active_request(record.request_id)
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}") from exc

    content_type = upstream_response.headers.get("content-type", "")
    is_streaming = "text/event-stream" in content_type.lower()
    headers_for_client = response_headers(upstream_response.headers)

    if not is_streaming:
        try:
            response_body = await upstream_response.aread()
        finally:
            await upstream_response.aclose()
            await client.aclose()
        record.status_code = upstream_response.status_code
        record.latency_ms = (time.perf_counter() - started) * 1000

        # All bookkeeping (capture, validation, tee write, verification enqueue)
        # happens after the response is handed back to the client.
        async def finalize_non_streaming() -> None:
            try:
                captured_response_body, response_truncated = capture_body(response_body, proxy.capture_bodies, proxy.max_body_bytes)
                record.response_body = captured_response_body
                record.response_truncated = response_truncated
                record.validation_issues = compute_validation_issues(
                    path, record.status_code, captured_response_body, response_truncated, config.validation.fields,
                    original_body,
                )
                await tee.update_active_request(record)
                await tee.safe_write_tee_record(proxy.db_path, record)
                if record.validation_issues:
                    await enqueue_verification(record, original_body, request_body)
            finally:
                await tee.remove_active_request(record.request_id)

        spawn_background_task(finalize_non_streaming(), f"finalize-{record.request_id}")
        return Response(
            content=response_body,
            status_code=upstream_response.status_code,
            headers=headers_for_client,
            media_type=upstream_response.headers.get("content-type"),
        )

    async def stream_and_capture() -> AsyncIterator[bytes]:
        captured = bytearray()
        response_truncated = False
        error = ""
        try:
            async for chunk in upstream_response.aiter_bytes():
                if proxy.capture_bodies and len(captured) < proxy.max_body_bytes:
                    remaining = proxy.max_body_bytes - len(captured)
                    captured.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        response_truncated = True
                elif proxy.capture_bodies and chunk:
                    response_truncated = True
                yield chunk
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            record.status_code = upstream_response.status_code
            record.latency_ms = (time.perf_counter() - started) * 1000
            record.error = error
            record.response_body = bytes(captured) if proxy.capture_bodies else None
            record.response_truncated = response_truncated
            await upstream_response.aclose()
            await client.aclose()

            # All remaining bookkeeping (SSE accumulation, validation, tee write,
            # verification enqueue) happens after the stream is handed back to the
            # client, mirroring the non-streaming path's finalize_non_streaming.
            async def finalize_streaming() -> None:
                try:
                    if not error and proxy.capture_bodies:
                        accumulated_content = extract_sse_content(bytes(captured))
                        if accumulated_content is not None:
                            record.validation_issues = compute_validation_issues(
                                path,
                                record.status_code,
                                synthesize_chat_completion_body(accumulated_content),
                                response_truncated,
                                config.validation.fields,
                                original_body,
                            )

                    await tee.update_active_request(record)
                    await tee.safe_write_tee_record(proxy.db_path, record)
                    if record.validation_issues:
                        await enqueue_verification(record, original_body, request_body)
                finally:
                    await tee.remove_active_request(record.request_id)

            spawn_background_task(finalize_streaming(), f"finalize-{record.request_id}")

    return StreamingResponse(
        stream_and_capture(),
        status_code=upstream_response.status_code,
        headers=headers_for_client,
        media_type=upstream_response.headers.get("content-type"),
    )


# Excluded from the OpenAPI schema: a single handler for five methods makes
# FastAPI emit duplicate operation IDs, and the pass-through proxy isn't part
# of the dashboard API surface anyway.
@router.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def openai_proxy(path: str, request: Request) -> Response:
    return await proxy_openai_request(path, request)
