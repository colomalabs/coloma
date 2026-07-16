import httpx
from fastapi.testclient import TestClient

from backend import auth, config, main, upstream


def set_key(monkeypatch, value: str) -> None:
    monkeypatch.setattr(auth, "BACKEND_API_KEY", value)


def test_requests_endpoint_rejects_missing_key(monkeypatch):
    set_key(monkeypatch, "secret-key")
    with TestClient(main.app) as client:
        response = client.get("/api/requests")
    assert response.status_code == 401


def test_requests_endpoint_rejects_wrong_key(monkeypatch):
    set_key(monkeypatch, "secret-key")
    with TestClient(main.app) as client:
        response = client.get("/api/requests", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_proxy_rejects_missing_key(monkeypatch):
    set_key(monkeypatch, "secret-key")
    with TestClient(main.app) as client:
        response = client.post("/v1/chat/completions", json={"model": "m"})
    assert response.status_code == 401


def test_pdf_render_rejects_missing_key(monkeypatch):
    set_key(monkeypatch, "secret-key")
    with TestClient(main.app) as client:
        response = client.post("/api/pdf/render", files={"file": ("doc.pdf", b"x", "application/pdf")})
    assert response.status_code == 401


def test_valid_key_grants_access(monkeypatch, tmp_path):
    set_key(monkeypatch, "secret-key")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    with TestClient(main.app) as client:
        response = client.get("/api/config", headers={"X-API-Key": "secret-key"})
    assert response.status_code == 200


def test_no_configured_key_leaves_endpoints_open(monkeypatch, tmp_path):
    set_key(monkeypatch, "")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    with TestClient(main.app) as client:
        response = client.get("/api/config")
    assert response.status_code == 200


def test_proxy_accepts_backend_key_as_bearer_and_swaps_upstream_auth(monkeypatch, tmp_path):
    set_key(monkeypatch, "secret-key")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    app_config = config.read_app_config()
    app_config.proxy.api_key = "upstream-key"
    app_config.proxy.db_path = str(tmp_path / "tee.sqlite3")
    config.write_app_config(app_config)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer upstream-key"
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))
    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "m"},
            headers={"Authorization": "Bearer secret-key"},
        )
    assert response.status_code == 200


def test_proxy_does_not_forward_backend_key_header_upstream(monkeypatch, tmp_path):
    set_key(monkeypatch, "secret-key")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    app_config = config.read_app_config()
    app_config.proxy.api_key = "upstream-key"
    app_config.proxy.db_path = str(tmp_path / "tee.sqlite3")
    config.write_app_config(app_config)

    def handler(request: httpx.Request) -> httpx.Response:
        assert auth.API_KEY_HEADER not in request.headers
        assert request.headers["authorization"] == "Bearer upstream-key"
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(upstream, "upstream_transport", httpx.MockTransport(handler))
    with TestClient(main.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "m"},
            headers={"X-API-Key": "secret-key"},
        )
    assert response.status_code == 200


def test_api_endpoints_accept_bearer_key(monkeypatch, tmp_path):
    set_key(monkeypatch, "secret-key")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    with TestClient(main.app) as client:
        response = client.get("/api/config", headers={"Authorization": "Bearer secret-key"})
    assert response.status_code == 200


def test_auth_check_rejects_missing_key(monkeypatch):
    set_key(monkeypatch, "secret-key")
    with TestClient(main.app) as client:
        response = client.get("/api/auth/check")
    assert response.status_code == 401


def test_auth_check_accepts_valid_key(monkeypatch):
    set_key(monkeypatch, "secret-key")
    with TestClient(main.app) as client:
        response = client.get("/api/auth/check", headers={"X-API-Key": "secret-key"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_read_backend_api_key_prefers_env_var(monkeypatch):
    monkeypatch.setenv(auth.API_KEY_ENV_VAR, "from-env")
    assert auth.read_backend_api_key() == "from-env"


def test_read_backend_api_key_parses_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv(auth.API_KEY_ENV_VAR, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nCOLOMA_API_KEY = \"from-file\"\n")
    monkeypatch.setattr(auth, "ENV_PATH", env_file)
    assert auth.read_backend_api_key() == "from-file"
