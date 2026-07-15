"""Request tee records: in-flight tracking and sqlite persistence."""

import asyncio
import json
import sqlite3
import time
from pathlib import Path

from pydantic import BaseModel, Field

from backend.logger import logger


class TeeRecord(BaseModel):
    request_id: str
    started_at: float
    method: str
    path: str
    query_string: str
    model: str
    status_code: int | None = None
    latency_ms: float | None = None
    error: str = ""
    request_body: bytes | None = None
    original_request_body: bytes | None = None
    response_body: bytes | None = None
    request_truncated: bool = False
    original_request_truncated: bool = False
    response_truncated: bool = False
    validation_issues: list[str] = Field(default_factory=list)


class RequestSummary(BaseModel):
    request_id: str
    started_at: float
    method: str
    path: str
    query_string: str
    model: str
    status_code: int | None = None
    latency_ms: float | None = None
    elapsed_ms: float | None = None
    error: str = ""
    request_body: str | None = None
    original_request_body: str | None = None
    response_body: str | None = None
    request_truncated: bool = False
    original_request_truncated: bool = False
    response_truncated: bool = False
    validation_issues: list[str] = Field(default_factory=list)
    running: bool = False
    # Body presence flags: list responses omit the (potentially huge) bodies
    # themselves; the detail endpoint returns them on demand.
    has_request_body: bool = False
    has_original_request_body: bool = False
    has_response_body: bool = False
    verification_status: str = ""  # "" | queued | running | done | error
    verification_resolved: bool | None = None
    verification_response_body: str | None = None
    verification_issues: list[str] = Field(default_factory=list)
    verification_error: str = ""


active_requests: dict[str, TeeRecord] = {}
active_requests_lock = asyncio.Lock()


def capture_body(body: bytes, enabled: bool, max_bytes: int) -> tuple[bytes | None, bool]:
    if not enabled:
        return None, False
    if len(body) <= max_bytes:
        return body, False
    return body[:max_bytes], True


def extract_model(body: bytes) -> str:
    if not body:
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    model = payload.get("model")
    return str(model).strip() if model is not None else ""


def decode_body(body: bytes | None) -> str | None:
    if body is None:
        return None
    return body.decode("utf-8", errors="replace")


_initialized_db_paths: set[str] = set()


def ensure_tee_db(db_path: str) -> None:
    """Run the schema DDL once per db path instead of on every read/write."""
    if db_path in _initialized_db_paths:
        return
    init_tee_db(db_path)
    _initialized_db_paths.add(db_path)


def init_tee_db(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS openai_request_tee (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                started_at REAL NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                query_string TEXT NOT NULL,
                model TEXT NOT NULL,
                status_code INTEGER,
                latency_ms REAL,
                error TEXT NOT NULL,
                request_body BLOB,
                response_body BLOB,
                request_truncated INTEGER NOT NULL,
                response_truncated INTEGER NOT NULL,
                validation_issues TEXT NOT NULL DEFAULT '[]',
                original_request_body BLOB,
                original_request_truncated INTEGER NOT NULL DEFAULT 0
            )
            """,
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_openai_request_tee_request_id ON openai_request_tee(request_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_openai_request_tee_started_at ON openai_request_tee(started_at)")
        existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(openai_request_tee)")}
        if "validation_issues" not in existing_columns:
            connection.execute("ALTER TABLE openai_request_tee ADD COLUMN validation_issues TEXT NOT NULL DEFAULT '[]'")
        if "original_request_body" not in existing_columns:
            connection.execute("ALTER TABLE openai_request_tee ADD COLUMN original_request_body BLOB")
        if "original_request_truncated" not in existing_columns:
            connection.execute("ALTER TABLE openai_request_tee ADD COLUMN original_request_truncated INTEGER NOT NULL DEFAULT 0")


def write_tee_record(db_path: str, record: TeeRecord) -> None:
    ensure_tee_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO openai_request_tee (
                request_id,
                started_at,
                method,
                path,
                query_string,
                model,
                status_code,
                latency_ms,
                error,
                request_body,
                response_body,
                request_truncated,
                response_truncated,
                validation_issues,
                original_request_body,
                original_request_truncated
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.request_id,
                record.started_at,
                record.method,
                record.path,
                record.query_string,
                record.model,
                record.status_code,
                record.latency_ms,
                record.error,
                record.request_body,
                record.response_body,
                int(record.request_truncated),
                int(record.response_truncated),
                json.dumps(record.validation_issues),
                record.original_request_body,
                int(record.original_request_truncated),
            ),
        )


async def safe_write_tee_record(db_path: str, record: TeeRecord) -> None:
    try:
        await asyncio.to_thread(write_tee_record, db_path, record)
    except Exception as exc:
        logger.error("Could not write OpenAI tee record %s: %s", record.request_id, exc)


async def track_active_request(record: TeeRecord) -> None:
    async with active_requests_lock:
        active_requests[record.request_id] = record.model_copy(deep=True)


async def update_active_request(record: TeeRecord) -> None:
    async with active_requests_lock:
        if record.request_id in active_requests:
            active_requests[record.request_id] = record.model_copy(deep=True)


async def remove_active_request(request_id: str) -> None:
    async with active_requests_lock:
        active_requests.pop(request_id, None)


def summarize_record(record: TeeRecord, running: bool = False, include_bodies: bool = True) -> RequestSummary:
    return RequestSummary(
        request_id=record.request_id,
        started_at=record.started_at,
        method=record.method,
        path=record.path,
        query_string=record.query_string,
        model=record.model,
        status_code=record.status_code,
        latency_ms=record.latency_ms,
        elapsed_ms=(time.time() - record.started_at) * 1000 if running else None,
        error=record.error,
        request_body=decode_body(record.request_body) if include_bodies else None,
        original_request_body=decode_body(record.original_request_body) if include_bodies else None,
        response_body=decode_body(record.response_body) if include_bodies else None,
        request_truncated=record.request_truncated,
        original_request_truncated=record.original_request_truncated,
        response_truncated=record.response_truncated,
        validation_issues=list(record.validation_issues),
        running=running,
        has_request_body=record.request_body is not None,
        has_original_request_body=record.original_request_body is not None,
        has_response_body=record.response_body is not None,
    )


def _saved_row_common_fields(row: sqlite3.Row) -> dict:
    return {
        "request_id": row["request_id"],
        "started_at": row["started_at"],
        "method": row["method"],
        "path": row["path"],
        "query_string": row["query_string"],
        "model": row["model"],
        "status_code": row["status_code"],
        "latency_ms": row["latency_ms"],
        "error": row["error"],
        "request_truncated": bool(row["request_truncated"]),
        "original_request_truncated": bool(row["original_request_truncated"]),
        "response_truncated": bool(row["response_truncated"]),
        "validation_issues": json.loads(row["validation_issues"]) if row["validation_issues"] else [],
        "running": False,
    }


def summarize_saved_row(row: sqlite3.Row) -> RequestSummary:
    """Build a full summary (bodies included) from a SELECT * row."""
    return RequestSummary(
        **_saved_row_common_fields(row),
        request_body=decode_body(row["request_body"]),
        original_request_body=decode_body(row["original_request_body"]),
        response_body=decode_body(row["response_body"]),
        has_request_body=row["request_body"] is not None,
        has_original_request_body=row["original_request_body"] is not None,
        has_response_body=row["response_body"] is not None,
    )


# Everything except the body blobs, plus presence flags — so listing history
# never reads megabytes of captured bodies off disk.
_SUMMARY_COLUMNS = """
    request_id, started_at, method, path, query_string, model, status_code, latency_ms, error,
    request_truncated, original_request_truncated, response_truncated, validation_issues,
    request_body IS NOT NULL AS has_request_body,
    original_request_body IS NOT NULL AS has_original_request_body,
    response_body IS NOT NULL AS has_response_body
"""


def list_saved_tee_records(db_path: str, limit: int) -> list[RequestSummary]:
    ensure_tee_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT {_SUMMARY_COLUMNS}
            FROM openai_request_tee
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        RequestSummary(
            **_saved_row_common_fields(row),
            has_request_body=bool(row["has_request_body"]),
            has_original_request_body=bool(row["has_original_request_body"]),
            has_response_body=bool(row["has_response_body"]),
        )
        for row in rows
    ]


def get_saved_tee_record(db_path: str, request_id: str) -> RequestSummary | None:
    ensure_tee_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM openai_request_tee WHERE request_id = ? ORDER BY id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
    return summarize_saved_row(row) if row is not None else None


async def list_active_request_summaries() -> list[RequestSummary]:
    async with active_requests_lock:
        records = [record.model_copy(deep=True) for record in active_requests.values()]
    return sorted(
        (summarize_record(record, running=True, include_bodies=False) for record in records),
        key=lambda item: item.started_at,
        reverse=True,
    )


async def get_active_request_summary(request_id: str) -> RequestSummary | None:
    async with active_requests_lock:
        record = active_requests.get(request_id)
        record = record.model_copy(deep=True) if record is not None else None
    return summarize_record(record, running=True) if record is not None else None
