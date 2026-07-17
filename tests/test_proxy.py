import asyncio
import base64
import gzip
import io
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import config, images, main, tasks, tee, upstream, validation, verification


@pytest.fixture(autouse=True)
def clear_active_requests():
    tee.active_requests.clear()
    yield
    tee.active_requests.clear()


def make_pdf_bytes(page_count: int = 2, size: tuple[int, int] = (200, 300)) -> bytes:
    pages = [Image.new("RGB", size, "white") for _ in range(page_count)]
    buffer = io.BytesIO()
    pages[0].save(buffer, format="PDF", save_all=True, append_images=pages[1:])
    return buffer.getvalue()


def write_config(path: Path, db_path: Path, max_body_bytes: int = 1_000_000) -> None:
    path.write_text(
        json.dumps(
            {
                "proxy": {
                    "base_url": "http://upstream",
                    "api_key": "configured-key",
                    "capture_bodies": True,
                    "max_body_bytes": max_body_bytes,
                    "db_path": str(db_path),
                },
            }
        ),
        encoding="utf-8",
    )


def records(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute("SELECT * FROM openai_request_tee ORDER BY id"))


def make_data_url(width: int, height: int) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color="red").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def image_size_from_data_url(data_url: str) -> tuple[int, int]:
    encoded = data_url.split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        return image.size


def write_config_with_max_mp(path: Path, db_path: Path, max_mp: float) -> None:
    path.write_text(
        json.dumps(
            {
                "proxy": {
                    "base_url": "http://upstream",
                    "api_key": "configured-key",
                    "capture_bodies": True,
                    "max_body_bytes": 1_000_000,
                    "db_path": str(db_path),
                },
                "optimization": {"max_mp": max_mp},
            }
        ),
        encoding="utf-8",
    )


def test_config_test_endpoint_reports_models(monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://upstream/v1/models"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]})

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post("/api/config/test", json={"base_url": "http://upstream", "api_key": "test-key"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "models": ["model-a", "model-b"], "detail": "Detected 2 model(s)", "error": ""}


def test_list_models_reports_models(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://upstream/v1/models"
        return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]})

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    assert response.json() == {"models": ["model-a", "model-b"], "error": ""}


def test_list_models_reports_error_when_upstream_unreachable(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["models"] == []
    assert "connection refused" in payload["error"]


def test_upstream_status_reports_connected(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://upstream/v1/models"
        return httpx.Response(200, json={"data": [{"id": "model-a"}]})

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "models": ["model-a"],
        "model_count": 1,
        "detail": "Detected 1 model(s)",
        "error": "",
    }


def test_upstream_status_reachable_but_no_models(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    # Reachable but nothing served yet: connected, but the model count is what lets the UI hold the
    # sidebar back from "ready" until a model actually comes online.
    assert payload["connected"] is True
    assert payload["models"] == []
    assert payload["model_count"] == 0


def test_upstream_status_reports_disconnected(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["models"] == []
    assert "connection refused" in payload["error"]


def test_non_streaming_proxy_forwards_and_captures(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "http://upstream/v1/chat/completions?trace=1"
        assert request.headers["authorization"] == "Bearer caller-key"
        assert json.loads(request.content) == {"model": "night-model", "messages": [{"role": "user", "content": "hi"}]}
        return httpx.Response(200, json={"id": "chatcmpl_1", "choices": []})

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions?trace=1",
            headers={"Authorization": "Bearer caller-key"},
            json={"model": "night-model", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200
    assert response.json() == {"id": "chatcmpl_1", "choices": []}

    [record] = records(db_path)
    assert record["method"] == "POST"
    assert record["path"] == "/v1/chat/completions"
    assert record["query_string"] == "trace=1"
    assert record["model"] == "night-model"
    assert record["status_code"] == 200
    assert b"caller-key" not in record["request_body"]
    assert json.loads(record["request_body"]) ["model"] == "night-model"
    assert json.loads(record["response_body"]) == {"id": "chatcmpl_1", "choices": []}


def test_non_streaming_proxy_removes_headers_for_decoded_upstream_body(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)
    body = b"hello world"
    compressed = gzip.compress(body)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/octet-stream",
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
            content=compressed,
        )

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.get("/v1/example")

    assert response.status_code == 200
    assert response.content == body
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(body))


def test_streaming_proxy_captures_after_stream(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: first\n\ndata: second\n\n",
        )

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        with client.stream("POST", "/v1/chat/completions", json={"model": "stream-model", "stream": True}) as response:
            body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert body == b"data: first\n\ndata: second\n\n"
    [record] = records(db_path)
    assert record["model"] == "stream-model"
    assert record["response_body"] == body


def test_streaming_proxy_removes_headers_for_decoded_upstream_body(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)
    body = b"data: first\n\ndata: second\n\n"
    compressed = gzip.compress(body)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
            content=compressed,
        )

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        with client.stream("GET", "/v1/example") as response:
            received = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert received == body
    assert "content-encoding" not in response.headers
    assert "content-length" not in response.headers


def test_capture_truncates_large_bodies(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path, max_body_bytes=8)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"response-too-long")

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post("/v1/completions", content=b"request-too-long")

    assert response.status_code == 200
    [record] = records(db_path)
    assert record["request_body"] == b"request-"
    assert record["response_body"] == b"response"
    assert record["request_truncated"] == 1
    assert record["response_truncated"] == 1


def test_upstream_error_is_preserved_and_captured(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post("/v1/chat/completions", json={"model": "busy"})

    assert response.status_code == 429
    assert response.json() == {"error": {"message": "rate limited"}}
    [record] = records(db_path)
    assert record["status_code"] == 429
    assert json.loads(record["response_body"]) == {"error": {"message": "rate limited"}}


def test_tee_write_failure_does_not_fail_proxy(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "missing" / "tee.sqlite3"
    write_config(config_path, db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    def broken_write(db_path: str, record: tee.TeeRecord) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(tee, "write_tee_record", broken_write)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {"ok": True}



def test_chat_completions_response_is_flagged_when_schema_mismatches(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    config_path.write_text(
        json.dumps(
            {
                "proxy": {
                    "base_url": "http://upstream",
                    "api_key": "configured-key",
                    "capture_bodies": True,
                    "max_body_bytes": 1_000_000,
                    "db_path": str(db_path),
                },
                "validation": {"fields": [{"name": "id", "type": "string"}, {"name": "choices", "type": "array"}]},
            }
        ),
        encoding="utf-8",
    )

    message_content = json.dumps({"id": 123, "choices": "not-an-array"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": message_content}}]},
        )

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "night-model",
                "response_format": {"type": "json_schema", "json_schema": {"name": "response", "schema": {}}},
            },
        )

    assert response.status_code == 200

    [record] = records(db_path)
    issues = json.loads(record["validation_issues"])
    assert "Field 'id' expected type 'string'" in issues
    assert "Field 'choices' expected type 'array'" in issues


def test_chat_completions_response_not_validated_without_structured_output_request(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    config_path.write_text(
        json.dumps(
            {
                "proxy": {
                    "base_url": "http://upstream",
                    "api_key": "configured-key",
                    "capture_bodies": True,
                    "max_body_bytes": 1_000_000,
                    "db_path": str(db_path),
                },
                "validation": {"fields": [{"name": "id", "type": "string"}, {"name": "choices", "type": "array"}]},
            }
        ),
        encoding="utf-8",
    )

    message_content = json.dumps({"id": 123, "choices": "not-an-array"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": message_content}}]},
        )

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post("/v1/chat/completions", json={"model": "night-model"})

    assert response.status_code == 200

    [record] = records(db_path)
    assert json.loads(record["validation_issues"]) == []


def test_chat_completions_response_matching_schema_has_no_issues(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    config_path.write_text(
        json.dumps(
            {
                "proxy": {
                    "base_url": "http://upstream",
                    "api_key": "configured-key",
                    "capture_bodies": True,
                    "max_body_bytes": 1_000_000,
                    "db_path": str(db_path),
                },
                "validation": {"fields": [{"name": "id", "type": "string"}]},
            }
        ),
        encoding="utf-8",
    )

    message_content = json.dumps({"id": "chatcmpl_1"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": message_content}}]},
        )

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post("/v1/chat/completions", json={"model": "night-model"})

    assert response.status_code == 200
    [record] = records(db_path)
    assert json.loads(record["validation_issues"]) == []


def sse_body_for_content(content: str) -> bytes:
    chunk = json.dumps({"choices": [{"delta": {"content": content}}]})
    return f"data: {chunk}\n\ndata: [DONE]\n\n".encode("utf-8")


def test_streaming_chat_completion_validates_accumulated_response(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    config_path.write_text(
        json.dumps(
            {
                "proxy": {
                    "base_url": "http://upstream",
                    "api_key": "configured-key",
                    "capture_bodies": True,
                    "max_body_bytes": 1_000_000,
                    "db_path": str(db_path),
                },
                "validation": {"fields": [{"name": "id", "type": "string"}, {"name": "choices", "type": "array"}]},
            }
        ),
        encoding="utf-8",
    )

    message_content = json.dumps({"id": 123, "choices": "not-an-array"})
    # Split the content across two delta chunks, like a real streaming response.
    half = len(message_content) // 2
    sse_body = (
        f"data: {json.dumps({'choices': [{'delta': {'content': message_content[:half]}}]})}\n\n"
        f"data: {json.dumps({'choices': [{'delta': {'content': message_content[half:]}}]})}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body)

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "stream-model",
                "stream": True,
                "response_format": {"type": "json_schema", "json_schema": {"name": "response", "schema": {}}},
            },
        ) as response:
            list(response.iter_bytes())

    assert response.status_code == 200
    [record] = records(db_path)
    issues = json.loads(record["validation_issues"])
    assert "Field 'id' expected type 'string'" in issues
    assert "Field 'choices' expected type 'array'" in issues


def test_streaming_chat_completion_matching_schema_has_no_issues(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    config_path.write_text(
        json.dumps(
            {
                "proxy": {
                    "base_url": "http://upstream",
                    "api_key": "configured-key",
                    "capture_bodies": True,
                    "max_body_bytes": 1_000_000,
                    "db_path": str(db_path),
                },
                "validation": {"fields": [{"name": "id", "type": "string"}]},
            }
        ),
        encoding="utf-8",
    )

    sse_body = sse_body_for_content(json.dumps({"id": "chatcmpl_1"}))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body)

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        with client.stream("POST", "/v1/chat/completions", json={"model": "stream-model", "stream": True}) as response:
            list(response.iter_bytes())

    assert response.status_code == 200
    [record] = records(db_path)
    assert json.loads(record["validation_issues"]) == []


def test_streaming_chat_completion_enqueues_verification_on_issues(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    config_path.write_text(
        json.dumps(
            {
                "proxy": {
                    "base_url": "http://upstream",
                    "api_key": "configured-key",
                    "capture_bodies": True,
                    "max_body_bytes": 1_000_000,
                    "db_path": str(db_path),
                },
                "validation": {"fields": [{"name": "id", "type": "string"}]},
            }
        ),
        encoding="utf-8",
    )

    sse_body = sse_body_for_content(json.dumps({"id": 123}))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body)

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "stream-model",
                "stream": True,
                "response_format": {"type": "json_schema", "json_schema": {"name": "response", "schema": {}}},
            },
        ) as response:
            list(response.iter_bytes())

        # enqueue_verification runs in a spawned background task (like the
        # non-streaming path's finalize_non_streaming). It may not be registered
        # yet when the stream closes, so poll until it has saved its result.
        async def wait_for_finalize() -> None:
            for _ in range(100):
                finalize_tasks = [task for task in tasks.background_tasks if task.get_name().startswith("finalize-")]
                if finalize_tasks:
                    await asyncio.gather(*finalize_tasks)
                if verification.list_saved_verifications(str(db_path), 10):
                    return
                await asyncio.sleep(0.05)

        client.portal.call(wait_for_finalize)

    assert response.status_code == 200
    saved_verifications = verification.list_saved_verifications(str(db_path), 10)
    assert len(saved_verifications) == 1
    assert saved_verifications[0].request_id
    assert saved_verifications[0].original_issues == ["Field 'id' expected type 'string'"]


def test_extract_sse_content_accumulates_deltas():
    body = (
        b'data: {"choices": [{"delta": {"content": "foo"}}]}\n\n'
        b'data: {"choices": [{"delta": {"content": "bar"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    assert validation.extract_sse_content(body) == "foobar"


def test_extract_sse_content_returns_none_when_no_content_deltas():
    body = b'data: {"choices": [{"delta": {}}]}\n\ndata: [DONE]\n\n'
    assert validation.extract_sse_content(body) is None


def test_force_non_streaming_body_disables_stream_flag():
    body = json.dumps({"model": "m", "stream": True, "messages": []}).encode("utf-8")
    result = validation.force_non_streaming_body(body)
    assert json.loads(result) == {"model": "m", "stream": False, "messages": []}


def test_force_non_streaming_body_drops_stream_options():
    body = json.dumps(
        {"model": "m", "stream": True, "stream_options": {"include_usage": True}, "messages": []}
    ).encode("utf-8")
    result = validation.force_non_streaming_body(body)
    assert json.loads(result) == {"model": "m", "stream": False, "messages": []}


def test_force_non_streaming_body_leaves_non_streaming_body_untouched():
    body = json.dumps({"model": "m", "messages": []}).encode("utf-8")
    assert validation.force_non_streaming_body(body) == body


def test_request_history_lists_active_and_saved(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)

    active = tee.TeeRecord(
        request_id="active-1",
        started_at=1_700_000_000,
        method="POST",
        path="/v1/chat/completions",
        query_string="",
        model="active-model",
        request_body=b'{"model":"active-model"}',
    )
    tee.active_requests[active.request_id] = active

    saved = tee.TeeRecord(
        request_id="saved-1",
        started_at=1_700_000_001,
        method="POST",
        path="/v1/completions",
        query_string="trace=1",
        model="saved-model",
        status_code=200,
        latency_ms=12.5,
        request_body=b'{"model":"saved-model"}',
        response_body=b'{"ok":true}',
    )
    tee.write_tee_record(str(db_path), saved)

    with TestClient(main.app) as client:
        response = client.get("/api/requests?limit=25")
        active_detail = client.get("/api/requests/active-1")
        saved_detail = client.get("/api/requests/saved-1")
        missing_detail = client.get("/api/requests/unknown")

    assert response.status_code == 200
    payload = response.json()
    # The list omits captured bodies and reports presence flags instead;
    # the detail endpoint returns the bodies.
    assert len(payload["active"]) == 1
    assert payload["active"][0]["request_id"] == "active-1"
    assert payload["active"][0]["running"] is True
    assert payload["active"][0]["request_body"] is None
    assert payload["active"][0]["has_request_body"] is True
    assert payload["active"][0]["has_response_body"] is False
    assert len(payload["saved"]) == 1
    assert payload["saved"][0]["request_id"] == "saved-1"
    assert payload["saved"][0]["running"] is False
    assert payload["saved"][0]["response_body"] is None
    assert payload["saved"][0]["has_response_body"] is True

    assert active_detail.status_code == 200
    assert active_detail.json()["request_body"] == '{"model":"active-model"}'
    assert saved_detail.status_code == 200
    assert saved_detail.json()["request_body"] == '{"model":"saved-model"}'
    assert saved_detail.json()["response_body"] == '{"ok":true}'
    assert missing_detail.status_code == 404


def test_large_images_are_downscaled_to_max_megapixels(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config_with_max_mp(config_path, db_path, max_mp=2.3)

    oversized = make_data_url(2550, 3611)
    forwarded_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        url = body["messages"][0]["content"][1]["image_url"]["url"]
        forwarded_urls.append(url)
        return httpx.Response(200, json={"id": "chatcmpl_1", "choices": []})

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "night-model",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "hi"},
                            {"type": "image_url", "image_url": {"url": oversized}},
                        ],
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert forwarded_urls[0] != oversized
    width, height = image_size_from_data_url(forwarded_urls[0])
    assert width * height <= 2.3 * 1_000_000
    assert width / height == pytest.approx(2550 / 3611, rel=1e-3)

    [record] = records(db_path)
    logged_url = json.loads(record["request_body"])["messages"][0]["content"][1]["image_url"]["url"]
    assert logged_url == forwarded_urls[0]
    original_logged_url = json.loads(record["original_request_body"])["messages"][0]["content"][1]["image_url"]["url"]
    assert original_logged_url == oversized


def test_images_within_mp_limit_are_untouched(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config_with_max_mp(config_path, db_path, max_mp=2.3)

    small = make_data_url(200, 100)
    forwarded_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        forwarded_urls.append(body["messages"][0]["content"][0]["image_url"]["url"])
        return httpx.Response(200, json={"id": "chatcmpl_1", "choices": []})

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "night-model",
                "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": small}}]}],
            },
        )

    assert response.status_code == 200
    assert forwarded_urls[0] == small


def test_default_config_disables_image_optimization(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config(config_path, db_path)

    oversized = make_data_url(2550, 3611)
    forwarded_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        forwarded_urls.append(body["messages"][0]["content"][0]["image_url"]["url"])
        return httpx.Response(200, json={"id": "chatcmpl_1", "choices": []})

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "night-model",
                "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": oversized}}]}],
            },
        )

    assert response.status_code == 200
    assert forwarded_urls[0] == oversized


def test_max_mp_zero_disables_optimization(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "tee.sqlite3"
    write_config_with_max_mp(config_path, db_path, max_mp=0)

    oversized = make_data_url(2550, 3611)
    forwarded_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        forwarded_urls.append(body["messages"][0]["content"][0]["image_url"]["url"])
        return httpx.Response(200, json={"id": "chatcmpl_1", "choices": []})

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))

    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "night-model",
                "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": oversized}}]}],
            },
        )

    assert response.status_code == 200
    assert forwarded_urls[0] == oversized


def test_render_pdf_returns_one_image_per_page(monkeypatch):
    pdf_bytes = make_pdf_bytes(page_count=2)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/pdf/render",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"dpi": "100", "black_and_white": "true", "quality": "80"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["images"]) == 2
    for image in payload["images"]:
        assert image.startswith("data:image/jpeg;base64,")


def test_render_pdf_defaults_match_300_dpi_and_quality_80(monkeypatch):
    pdf_bytes = make_pdf_bytes(page_count=1)
    captured = {}

    def fake_render(pdf_bytes: bytes, dpi: int, black_and_white: bool, quality: int) -> list[str]:
        captured["dpi"] = dpi
        captured["black_and_white"] = black_and_white
        captured["quality"] = quality
        return ["data:image/jpeg;base64,AA=="]

    monkeypatch.setattr(images, "render_pdf_pages", fake_render)

    with TestClient(main.app) as client:
        response = client.post("/api/pdf/render", files={"file": ("doc.pdf", pdf_bytes, "application/pdf")})

    assert response.status_code == 200
    assert captured == {"dpi": 300, "black_and_white": True, "quality": 80}


def test_render_pdf_rejects_invalid_file():
    with TestClient(main.app) as client:
        response = client.post(
            "/api/pdf/render",
            files={"file": ("doc.pdf", b"not a real pdf", "application/pdf")},
        )

    assert response.status_code == 400


def test_render_pdf_clamps_dpi_and_quality(monkeypatch):
    pdf_bytes = make_pdf_bytes(page_count=1)
    captured = {}

    def fake_render(pdf_bytes: bytes, dpi: int, black_and_white: bool, quality: int) -> list[str]:
        captured["dpi"] = dpi
        captured["quality"] = quality
        return []

    monkeypatch.setattr(images, "render_pdf_pages", fake_render)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/pdf/render",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"dpi": "5000", "quality": "0"},
        )

    assert response.status_code == 200
    assert captured == {"dpi": images.PDF_MAX_DPI, "quality": images.PDF_MIN_QUALITY}
