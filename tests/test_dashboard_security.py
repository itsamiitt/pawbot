"""Dashboard auth regression tests."""

from __future__ import annotations

from starlette.testclient import TestClient

from pawbot.dashboard import auth as dashboard_auth
from pawbot.dashboard.server import app


def test_dashboard_login_flow_requires_auth(tmp_path, monkeypatch) -> None:
    """Protected dashboard APIs should require login, then succeed with a session cookie."""
    monkeypatch.setattr(dashboard_auth, "AUTH_FILE", tmp_path / "dashboard_auth.json")
    monkeypatch.setattr(dashboard_auth, "JWT_SECRET_FILE", tmp_path / "dashboard_secret")
    monkeypatch.setattr(dashboard_auth, "AUTH_STORAGE_DIR", tmp_path / "dashboard_tokens")
    dashboard_auth.set_password("topsecret")

    client = TestClient(app)

    status = client.get("/api/auth/status")
    assert status.status_code == 200
    assert status.json() == {"authenticated": False, "configured": True}

    denied = client.get("/api/overview")
    assert denied.status_code == 401
    assert denied.json()["error"] == "Auth required"

    bad_login = client.post("/api/auth/login", json={"password": "wrong"})
    assert bad_login.status_code == 401

    login = client.post("/api/auth/login", json={"password": "topsecret"})
    assert login.status_code == 200
    assert "pawbot_session" in client.cookies

    allowed = client.get("/api/overview")
    assert allowed.status_code == 200
    assert "agent_status" in allowed.json()

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    denied_again = client.get("/api/overview")
    assert denied_again.status_code == 401


def test_dashboard_status_reflects_authenticated_session(tmp_path, monkeypatch) -> None:
    """The public auth status route should reflect an active signed-in session."""
    monkeypatch.setattr(dashboard_auth, "AUTH_FILE", tmp_path / "dashboard_auth.json")
    monkeypatch.setattr(dashboard_auth, "JWT_SECRET_FILE", tmp_path / "dashboard_secret")
    monkeypatch.setattr(dashboard_auth, "AUTH_STORAGE_DIR", tmp_path / "dashboard_tokens")
    dashboard_auth.set_password("another-secret")

    client = TestClient(app)
    client.post("/api/auth/login", json={"password": "another-secret"})

    status = client.get("/api/auth/status")
    assert status.status_code == 200
    assert status.json() == {"authenticated": True, "configured": True}
