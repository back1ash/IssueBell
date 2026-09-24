"""Browser and DB coverage for sliding login sessions and reauthentication."""

import base64
import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.config import settings
from app.database import get_db
from app.models import AuthSession, Base, User
from app.routers import auth


@pytest.fixture()
def login_env(monkeypatch):
    now = datetime(2026, 9, 24, 12)
    monkeypatch.setattr(auth, "_utcnow", lambda: now)
    monkeypatch.setattr(settings, "session_max_age", 90 * 86400)
    monkeypatch.setattr(settings, "session_absolute_max_age", 180 * 86400)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (main, auth):
        monkeypatch.setattr(module, "SessionLocal", factory)
    def test_db():
        with factory() as db:
            yield db
    monkeypatch.setitem(main.app.dependency_overrides, get_db, test_db)
    with factory() as db:
        db.add(User(id=1, discord_id="123456", username="tester"))
        db.commit()
    yield now, factory
    engine.dispose()


def browser(factory, now, *, age=10, remaining=5, ip="192.0.2.1"):
    with factory() as db:
        db.add(AuthSession(
            id="login", user_id=1, created_at=now - timedelta(days=age),
            expires_at=now + timedelta(days=remaining),
        ))
        db.commit()
    client = TestClient(main.app, base_url="https://testserver", client=(ip, 1234))
    payload = base64.b64encode(json.dumps({
        "user_id": 1, auth.AUTH_SESSION_KEY: "login", "_csrf_token": "x" * 32,
    }).encode())
    client.cookies.set("issuebell_session", TimestampSigner(settings.secret_key).sign(payload).decode(), domain="testserver.local", path="/")
    return client


def test_dashboard_renews_legacy_session_and_network_change_preserves_login(login_env):
    now, factory = login_env
    client = browser(factory, now)
    response = client.get("/")
    assert 'class="container dashboard-page"' in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert "Cookie" in response.headers["vary"]
    assert any("issuebell_session=" in h for h in response.headers.get_list("set-cookie"))
    with factory() as db:
        session = db.get(AuthSession, "login")
        assert session.expires_at == now + timedelta(days=90)
        assert session.created_at == now - timedelta(days=10)
    other_network = TestClient(main.app, base_url="https://testserver", client=("198.51.100.1", 1234))
    other_network.cookies.update(client.cookies)
    assert 'class="container dashboard-page"' in other_network.get("/").text


def test_renewal_is_capped_at_original_login_plus_180_days(login_env):
    now, factory = login_env
    client = browser(factory, now, age=179, remaining=0.5)
    assert 'class="container dashboard-page"' in client.get("/").text
    with factory() as db:
        assert db.get(AuthSession, "login").expires_at == now + timedelta(days=1)


@pytest.mark.parametrize("age,remaining", [(90, 0), (180, 10), (181, 10)])
def test_idle_or_absolute_expiry_requires_reauthentication(login_env, age, remaining):
    now, factory = login_env
    client = browser(factory, now, age=age, remaining=remaining)
    response = client.get("/")
    assert "Your session has expired." in response.text
    assert "Continue with Discord" in response.text
    assert "Catch the right issue." not in response.text
    assert 'class="container dashboard-page"' not in response.text
    assert client.get("/subscriptions/status").status_code == 401
    assert "Your session has expired." in client.get("/").text
    with factory() as db:
        assert db.get(AuthSession, "login").expires_at == now + timedelta(days=remaining)


def test_polling_does_not_extend_idle_expiry(login_env):
    now, factory = login_env
    client = browser(factory, now)
    assert client.get("/subscriptions/status").status_code == 200
    with factory() as db:
        assert db.get(AuthSession, "login").expires_at == now + timedelta(days=5)


def test_browser_hint_cannot_authenticate_and_new_visitors_see_landing(login_env):
    client = TestClient(main.app, base_url="https://testserver")
    assert "Catch the right issue." in client.get("/").text
    client.cookies.set(auth.RETURNING_COOKIE, "1")
    assert "Your session has expired." in client.get("/").text
    assert client.get("/subscriptions/status").status_code == 401


def test_logout_revokes_session_and_clears_returning_hint(login_env):
    now, factory = login_env
    client = browser(factory, now)
    client.get("/")
    response = client.post("/auth/logout", headers={"X-CSRF-Token": "x" * 32})
    assert "Catch the right issue." in response.text
    assert auth.RETURNING_COOKIE not in client.cookies
    with factory() as db:
        assert db.get(AuthSession, "login") is None


def test_new_session_uses_configured_limits(login_env):
    now, factory = login_env
    with factory() as db:
        session_id = auth._create_auth_session(db, 1)
        db.commit()
        session = db.get(AuthSession, session_id)
        assert session.created_at == now
        assert session.expires_at == now + timedelta(days=90)


def test_discord_reauthentication_returns_to_watchlist(login_env, monkeypatch):
    now, factory = login_env
    client = browser(factory, now, remaining=-1)
    assert "Your session has expired." in client.get("/").text
    monkeypatch.setattr(settings, "discord_client_id", "test-client")
    monkeypatch.setattr(settings, "discord_client_secret", "test-secret")

    class DiscordClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            return httpx.Response(200, json={"access_token": "test-token"}, request=httpx.Request("POST", url))

        async def get(self, url, **kwargs):
            return httpx.Response(200, json={"id": "123456", "username": "tester"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(auth.httpx, "AsyncClient", DiscordClient)
    start = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    response = client.get("/auth/callback", params={"code": "test-code", "state": state})
    assert response.url.path == "/"
    assert 'class="container dashboard-page"' in response.text
    assert client.cookies.get(auth.RETURNING_COOKIE) == "1"
    with factory() as db:
        sessions = db.query(AuthSession).all()
        assert len(sessions) == 1
        assert sessions[0].created_at == now
        assert sessions[0].expires_at == now + timedelta(days=90)


def test_revoked_session_cannot_be_renewed(login_env):
    now, factory = login_env
    client = browser(factory, now)
    with factory() as db:
        db.query(AuthSession).delete()
        db.commit()
    assert "Your session has expired." in client.get("/").text
    with factory() as db:
        assert db.query(AuthSession).count() == 0


def test_expired_browser_cookie_without_hint_shows_reauthentication(login_env):
    client = TestClient(main.app, base_url="https://testserver")
    client.cookies.set("issuebell_session", "invalid-or-old-signature", domain="testserver.local", path="/")
    assert "Your session has expired." in client.get("/").text
    assert client.get("/subscriptions/status").status_code == 401
