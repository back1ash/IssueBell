"""Focused regression tests for authentication and browser security controls."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import auth


@pytest.fixture()
def security_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(settings, "discord_client_id", "discord-client")
    monkeypatch.setattr(settings, "discord_client_secret", "discord-secret")
    monkeypatch.setattr(
        settings,
        "discord_redirect_uri",
        "http://testserver/auth/callback",
    )
    monkeypatch.setattr(settings, "github_client_id", "github-client")
    monkeypatch.setattr(settings, "github_client_secret", "github-secret")
    # These focused OAuth tests use a lightweight fake login. Server-side
    # session persistence is covered by the app integration path.
    monkeypatch.setattr(
        auth,
        "authenticated_user_id",
        lambda request, db: request.session.get("user_id"),
    )

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    app.include_router(auth.router)

    @app.get("/_csrf")
    async def issue_csrf(request: Request):
        request.session["user_id"] = 123
        return {"csrf_token": auth.ensure_csrf_token(request)}

    @app.post("/_csrf-protected")
    async def csrf_protected(request: Request):
        await auth.require_csrf_token(request)
        return {"ok": True}

    @app.get("/_user/{user_id}")
    async def switch_test_user(request: Request, user_id: int):
        request.session["user_id"] = user_id
        return {"user_id": user_id}

    return app


def _start_discord_login(client: TestClient) -> str:
    response = client.get("/auth/login")
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://discord.com/oauth2/authorize?")
    return parse_qs(urlparse(location).query)["state"][0]


def test_oauth_state_is_bound_to_the_browser_session(security_app: FastAPI) -> None:
    originating_browser = TestClient(security_app, follow_redirects=False)
    other_browser = TestClient(security_app, follow_redirects=False)
    state = _start_discord_login(originating_browser)

    response = other_browser.get(
        "/auth/callback",
        params={"state": state, "error": "access_denied"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?error=invalid_state"


def test_oauth_state_is_provider_specific(security_app: FastAPI) -> None:
    client = TestClient(security_app, follow_redirects=False)
    discord_state = _start_discord_login(client)

    response = client.get(
        "/auth/github/callback",
        params={"state": discord_state, "error": "access_denied"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?error=invalid_state"


def test_github_state_is_bound_to_the_authenticated_user(
    security_app: FastAPI,
) -> None:
    client = TestClient(security_app, follow_redirects=False)
    client.get("/_user/123")
    start = client.get("/auth/github")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get("/_user/456")

    response = client.get(
        "/auth/github/callback",
        params={"state": state, "code": "not-exchanged"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?error=invalid_state"


def test_provider_error_text_is_not_reflected_and_state_is_consumed(
    security_app: FastAPI,
) -> None:
    client = TestClient(security_app, follow_redirects=False)
    state = _start_discord_login(client)
    hostile_description = '<img src=x onerror="alert(1)">'

    denied = client.get(
        "/auth/callback",
        params={
            "state": state,
            "error": "access_denied",
            "error_description": hostile_description,
        },
    )
    replay = client.get(
        "/auth/callback",
        params={"state": state, "error": "access_denied"},
    )

    assert denied.status_code == 303
    assert denied.headers["location"] == "/?error=oauth_denied"
    assert hostile_description not in denied.headers["location"]
    assert replay.headers["location"] == "/?error=invalid_state"


@pytest.mark.parametrize(
    "path",
    ["/auth/logout", "/auth/github/disconnect", "/auth/delete-account"],
)
def test_state_changing_get_routes_are_rejected(
    security_app: FastAPI,
    path: str,
) -> None:
    response = TestClient(security_app, follow_redirects=False).get(path)

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


def test_csrf_token_is_required_and_session_bound(security_app: FastAPI) -> None:
    client = TestClient(security_app, follow_redirects=False)
    token = client.get("/_csrf").json()["csrf_token"]

    assert client.post("/_csrf-protected").status_code == 403
    assert (
        client.post("/_csrf-protected", data={"csrf_token": "wrong-token"}).status_code
        == 403
    )
    assert (
        client.post("/_csrf-protected", data={"csrf_token": "잘못된-토큰"}).status_code
        == 403
    )
    assert (
        client.post("/_csrf-protected", data={"csrf_token": token}).status_code
        == 200
    )

    unrelated_browser = TestClient(security_app, follow_redirects=False)
    assert (
        unrelated_browser.post(
            "/_csrf-protected",
            data={"csrf_token": token},
        ).status_code
        == 403
    )


def test_logout_requires_csrf_and_clears_the_session(security_app: FastAPI) -> None:
    client = TestClient(security_app, follow_redirects=False)
    token = client.get("/_csrf").json()["csrf_token"]

    assert client.post("/auth/logout").status_code == 403
    response = client.post("/auth/logout", data={"csrf_token": token})

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # A repeated unauthenticated logout is harmless and does not need a token.
    assert client.post("/auth/logout").status_code == 303


def test_delete_account_cascades_and_revokes_token_best_effort(
    security_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeUser:
        github_token = "github-token"

    class FakeSession:
        def __init__(self) -> None:
            self.user = FakeUser()
            self.deleted = None
            self.committed = False

        def get(self, _model, user_id):
            return self.user if user_id == 123 else None

        def delete(self, user):
            self.deleted = user

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("rollback was not expected")

        def close(self):
            pass

    fake_session = FakeSession()
    revoked: list[str | None] = []

    async def fake_revoke(token: str | None) -> bool:
        revoked.append(token)
        return True

    monkeypatch.setattr(auth, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(auth, "_revoke_github_token", fake_revoke)
    client = TestClient(security_app, follow_redirects=False)
    token = client.get("/_csrf").json()["csrf_token"]

    response = client.post(
        "/auth/delete-account",
        data={"csrf_token": token},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?account_deleted=1"
    assert fake_session.deleted is fake_session.user
    assert fake_session.committed is True
    assert revoked == ["github-token"]


def test_admin_template_does_not_interpolate_user_data_into_html() -> None:
    template = (
        Path(__file__).resolve().parents[1] / "app" / "templates" / "manage.html"
    ).read_text(encoding="utf-8")

    assert ".innerHTML" not in template
    assert "textContent" in template
    assert "safeGitHubRepoUrl" in template
    assert "safeDiscordAvatar" in template
