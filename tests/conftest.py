import pytest

from backend import auth


@pytest.fixture(autouse=True)
def disable_backend_api_key(monkeypatch):
    # Keep endpoint tests independent of any local backend/.env; auth tests
    # set an explicit key themselves.
    monkeypatch.setattr(auth, "BACKEND_API_KEY", "")
