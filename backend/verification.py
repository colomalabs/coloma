"""Re-verification of failed structured-output requests against the upstream."""

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from backend.config import read_app_config
from backend.logger import logger
from backend.tee import TeeRecord, active_requests, active_requests_lock, capture_body, decode_body
from backend.upstream import get_upstream_transport, proxy_upstream_url
from backend.validation import compute_validation_issues, force_non_streaming_body


class VerificationRecord(BaseModel):
    verification_id: str
    request_id: str
    created_at: float
    status: str = "queued"  # queued | running | done | error
    method: str = "POST"
    path: str = ""
    model: str = ""
    latency_ms: float | None = None
    error: str = ""
    original_request_body: bytes | None = None
    optimized_request_body: bytes | None = None
    original_response_body: bytes | None = None
    new_response_body: bytes | None = None
    original_issues: list[str] = Field(default_factory=list)
    new_issues: list[str] = Field(default_factory=list)
    resolved: bool | None = None


class VerificationSummary(BaseModel):
    verification_id: str
    request_id: str
    created_at: float
    status: str
    method: str
    path: str
    model: str
    latency_ms: float | None = None
    error: str = ""
    original_request_body: str | None = None
    optimized_request_body: str | None = None
    original_response_body: str | None = None
    new_response_body: str | None = None
    original_issues: list[str] = Field(default_factory=list)
    new_issues: list[str] = Field(default_factory=list)
    resolved: bool | None = None


verification_jobs: dict[str, VerificationRecord] = {}
verification_queue: asyncio.Queue[str] = asyncio.Queue()
verification_lock = asyncio.Lock()


def reset_verification_queue() -> None:
    """Create a fresh queue for the current event loop (called from the app lifespan).

    Jobs that were enqueued but never ran (e.g. under a previous app instance)
    are re-enqueued so they aren't lost.
    """
    global verification_queue
    queue: asyncio.Queue[str] = asyncio.Queue()
    for verification_id, job in verification_jobs.items():
        if job.status == "queued":
            queue.put_nowait(verification_id)
    verification_queue = queue


_initialized_db_paths: set[str] = set()


def ensure_verification_db(db_path: str) -> None:
    """Run the schema DDL once per db path instead of on every read/write."""
    if db_path in _initialized_db_paths:
        return
    init_verification_db(db_path)
    _initialized_db_paths.add(db_path)


def init_verification_db(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS verification_tee (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verification_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                status TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                model TEXT NOT NULL,
                latency_ms REAL,
                error TEXT NOT NULL,
                original_request_body BLOB,
                optimized_request_body BLOB,
                original_response_body BLOB,
                new_response_body BLOB,
                original_issues TEXT NOT NULL DEFAULT '[]',
                new_issues TEXT NOT NULL DEFAULT '[]',
                resolved INTEGER
            )
            """,
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_verification_tee_created_at ON verification_tee(created_at)")


def write_verification_record(db_path: str, record: VerificationRecord) -> None:
    ensure_verification_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO verification_tee (
                verification_id, request_id, created_at, status, method, path, model,
                latency_ms, error, original_request_body, optimized_request_body,
                original_response_body, new_response_body, original_issues, new_issues, resolved
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.verification_id,
                record.request_id,
                record.created_at,
                record.status,
                record.method,
                record.path,
                record.model,
                record.latency_ms,
                record.error,
                record.original_request_body,
                record.optimized_request_body,
                record.original_response_body,
                record.new_response_body,
                json.dumps(record.original_issues),
                json.dumps(record.new_issues),
                None if record.resolved is None else int(record.resolved),
            ),
        )


def summarize_verification(record: VerificationRecord, include_bodies: bool = True) -> VerificationSummary:
    return VerificationSummary(
        verification_id=record.verification_id,
        request_id=record.request_id,
        created_at=record.created_at,
        status=record.status,
        method=record.method,
        path=record.path,
        model=record.model,
        latency_ms=record.latency_ms,
        error=record.error,
        original_request_body=decode_body(record.original_request_body) if include_bodies else None,
        optimized_request_body=decode_body(record.optimized_request_body) if include_bodies else None,
        original_response_body=decode_body(record.original_response_body) if include_bodies else None,
        new_response_body=decode_body(record.new_response_body) if include_bodies else None,
        original_issues=list(record.original_issues),
        new_issues=list(record.new_issues),
        resolved=record.resolved,
    )


def summarize_verification_row(row: sqlite3.Row) -> VerificationSummary:
    return VerificationSummary(
        verification_id=row["verification_id"],
        request_id=row["request_id"],
        created_at=row["created_at"],
        status=row["status"],
        method=row["method"],
        path=row["path"],
        model=row["model"],
        latency_ms=row["latency_ms"],
        error=row["error"],
        original_request_body=decode_body(row["original_request_body"]),
        optimized_request_body=decode_body(row["optimized_request_body"]),
        original_response_body=decode_body(row["original_response_body"]),
        new_response_body=decode_body(row["new_response_body"]),
        original_issues=json.loads(row["original_issues"]) if row["original_issues"] else [],
        new_issues=json.loads(row["new_issues"]) if row["new_issues"] else [],
        resolved=None if row["resolved"] is None else bool(row["resolved"]),
    )


# Everything except the body blobs — listing history never reads captured
# bodies off disk; the request-detail endpoint fetches them on demand.
_SUMMARY_COLUMNS = """
    verification_id, request_id, created_at, status, method, path, model,
    latency_ms, error, original_issues, new_issues, resolved
"""


def list_saved_verifications(db_path: str, limit: int) -> list[VerificationSummary]:
    ensure_verification_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT {_SUMMARY_COLUMNS} FROM verification_tee ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        VerificationSummary(
            verification_id=row["verification_id"],
            request_id=row["request_id"],
            created_at=row["created_at"],
            status=row["status"],
            method=row["method"],
            path=row["path"],
            model=row["model"],
            latency_ms=row["latency_ms"],
            error=row["error"],
            original_issues=json.loads(row["original_issues"]) if row["original_issues"] else [],
            new_issues=json.loads(row["new_issues"]) if row["new_issues"] else [],
            resolved=None if row["resolved"] is None else bool(row["resolved"]),
        )
        for row in rows
    ]


def get_saved_verification(db_path: str, request_id: str) -> VerificationSummary | None:
    ensure_verification_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM verification_tee WHERE request_id = ? ORDER BY id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
    return summarize_verification_row(row) if row is not None else None


async def enqueue_verification(record: TeeRecord, original_body: bytes, optimized_body: bytes) -> None:
    job = VerificationRecord(
        verification_id=str(uuid.uuid4()),
        request_id=record.request_id,
        created_at=time.time(),
        method=record.method,
        path=record.path,
        model=record.model,
        original_request_body=original_body,
        optimized_request_body=optimized_body,
        original_response_body=record.response_body,
        original_issues=list(record.validation_issues),
    )
    async with verification_lock:
        verification_jobs[job.verification_id] = job
    await verification_queue.put(job.verification_id)


async def wait_for_idle_proxy() -> None:
    while True:
        async with active_requests_lock:
            busy = bool(active_requests)
        if not busy:
            return
        await asyncio.sleep(0.5)


async def run_verification(job: VerificationRecord) -> None:
    config = read_app_config()
    proxy = config.proxy
    path = job.path.removeprefix("/v1/")
    upstream_url = proxy_upstream_url(proxy.base_url, path)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None), transport=get_upstream_transport()) as client:
            response = await client.post(
                upstream_url,
                # Force a synchronous response even if the original request streamed,
                # since compute_validation_issues expects a single JSON body.
                content=force_non_streaming_body(job.original_request_body),
                headers={
                    "Authorization": f"Bearer {proxy.api_key}",
                    "Content-Type": "application/json",
                },
            )
            body = response.content
        job.latency_ms = (time.perf_counter() - started) * 1000
        captured_body, truncated = capture_body(body, proxy.capture_bodies, proxy.max_body_bytes)
        job.new_response_body = captured_body
        job.new_issues = compute_validation_issues(
            path, response.status_code, captured_body, truncated, config.validation.fields,
            job.original_request_body,
        )
        if response.status_code >= 400:
            job.status = "error"
            job.error = f"Upstream returned status {response.status_code}"
        else:
            job.status = "done"
            job.resolved = not job.new_issues
    except Exception as exc:
        job.latency_ms = (time.perf_counter() - started) * 1000
        job.status = "error"
        job.error = str(exc)

    try:
        await asyncio.to_thread(write_verification_record, proxy.db_path, job)
    except Exception as exc:
        logger.error("Could not write verification record %s: %s", job.verification_id, exc)
    async with verification_lock:
        verification_jobs.pop(job.verification_id, None)


async def verification_worker() -> None:
    while True:
        verification_id = await verification_queue.get()
        try:
            async with verification_lock:
                job = verification_jobs.get(verification_id)
            if job is None:
                continue
            # Only run verification while no proxied requests are in flight,
            # and re-check before every job.
            await wait_for_idle_proxy()
            job.status = "running"
            await run_verification(job)
        except Exception as exc:
            logger.error("Verification worker error: %s", exc)
        finally:
            verification_queue.task_done()
