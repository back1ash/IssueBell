"""Discord + GitHub OAuth2 authentication and account security flows."""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import AuthSession, User
from app.services.discord import build_welcome_message, send_dm
from app.token_crypto import TokenDecryptionError

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"
GITHUB_API = "https://api.github.com"
DISCORD_SCOPES = "identify applications.commands"
GITHUB_SCOPES = "read:user"  # public repository access needs no repository scope
STATE_MAX_AGE = settings.oauth_state_max_age

CSRF_SESSION_KEY = "_csrf_token"
OAUTH_STATES_SESSION_KEY = "_oauth_states"
AUTH_SESSION_KEY = "_auth_session_id"
MAX_PENDING_STATES_PER_PROVIDER = 4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def authenticated_user_id(request: Request, db: Session) -> int | None:
    """Return a user id only while its revocable server-side session is valid."""

    user_id = request.session.get("user_id")
    session_id = request.session.get(AUTH_SESSION_KEY)
    if (
        not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or not isinstance(session_id, str)
        or not session_id
    ):
        request.session.clear()
        return None

    exists = db.query(AuthSession.id).filter(
        AuthSession.id == session_id,
        AuthSession.user_id == user_id,
        AuthSession.expires_at > _utcnow(),
    ).first()
    if exists is None:
        request.session.clear()
        return None
    return user_id


def _create_auth_session(db: Session, user_id: int) -> str:
    session_id = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            id=session_id,
            user_id=user_id,
            expires_at=_utcnow() + timedelta(seconds=settings.session_max_age),
        )
    )
    # Opportunistic cleanup prevents expired login records growing forever.
    db.query(AuthSession).filter(AuthSession.expires_at <= _utcnow()).delete(
        synchronize_session=False
    )
    return session_id


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="oauth2-state")


def _no_store_redirect(url: str, *, status_code: int = 303) -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _error_redirect(code: str) -> RedirectResponse:
    """Redirect with an application-owned error code, never provider input."""
    query = urlencode({"error": code})
    return _no_store_redirect(f"/?{query}")


def _success_redirect(**params: str) -> RedirectResponse:
    url = "/"
    if params:
        url = f"/?{urlencode(params)}"
    return _no_store_redirect(url)


def ensure_csrf_token(request: Request) -> str:
    """Return the session CSRF token, creating a cryptographically random one."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def require_csrf_token(request: Request) -> None:
    """Validate a form/header CSRF token using a constant-time comparison."""
    expected = request.session.get(CSRF_SESSION_KEY)
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied:
        try:
            form = await request.form()
            form_value = form.get("csrf_token", "")
            supplied = form_value if isinstance(form_value, str) else ""
        except Exception:
            supplied = ""

    structurally_valid = (
        isinstance(expected, str)
        and 32 <= len(expected) <= 128
        and isinstance(supplied, str)
        and 1 <= len(supplied) <= 128
    )
    tokens_match = (
        compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))
        if structurally_valid
        else False
    )
    if (
        not isinstance(expected, str)
        or not expected
        or not isinstance(supplied, str)
        or not supplied
        or not tokens_match
    ):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _pending_states(request: Request) -> dict[str, list[str]]:
    """Read only well-formed pending state values from the signed session."""
    raw = request.session.get(OAUTH_STATES_SESSION_KEY)
    if not isinstance(raw, dict):
        return {}

    clean: dict[str, list[str]] = {}
    for provider, nonces in raw.items():
        if not isinstance(provider, str) or not isinstance(nonces, list):
            continue
        clean[provider] = [nonce for nonce in nonces if isinstance(nonce, str)]
    return clean


def _create_oauth_state(
    request: Request,
    provider: str,
    *,
    user_id: int | None = None,
) -> str:
    """Create a signed state that is also bound to this browser session."""
    nonce = secrets.token_urlsafe(32)
    states = _pending_states(request)
    provider_states = states.get(provider, [])
    provider_states.append(nonce)
    states[provider] = provider_states[-MAX_PENDING_STATES_PER_PROVIDER:]
    request.session[OAUTH_STATES_SESSION_KEY] = states

    payload: dict[str, Any] = {"provider": provider, "nonce": nonce}
    if user_id is not None:
        payload["uid"] = user_id
    return _signer().dumps(payload)


def _consume_oauth_state(
    request: Request,
    state: str,
    expected_provider: str,
) -> dict[str, Any] | None:
    """Validate provider/session binding and consume a pending OAuth state."""
    if not state:
        return None
    try:
        payload = _signer().loads(state, max_age=settings.oauth_state_max_age)
    except (BadSignature, SignatureExpired):
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("provider") != expected_provider:
        return None
    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        return None

    states = _pending_states(request)
    provider_states = states.get(expected_provider, [])
    matched = any(compare_digest(nonce, pending) for pending in provider_states)
    if not matched:
        return None

    states[expected_provider] = [
        pending
        for pending in provider_states
        if not compare_digest(nonce, pending)
    ]
    if not states[expected_provider]:
        states.pop(expected_provider, None)
    request.session[OAUTH_STATES_SESSION_KEY] = states
    return payload


def _method_not_allowed() -> Response:
    return Response(
        content="Method Not Allowed",
        status_code=405,
        media_type="text/plain",
        headers={"Allow": "POST"},
    )


async def _revoke_github_token(access_token: str | None) -> bool:
    """Best-effort deletion of an OAuth app token at GitHub."""
    if (
        not access_token
        or not settings.github_client_id
        or not settings.github_client_secret
    ):
        return False

    try:
        async with httpx.AsyncClient(timeout=settings.oauth_http_timeout) as client:
            response = await client.request(
                "DELETE",
                f"{GITHUB_API}/applications/{settings.github_client_id}/token",
                auth=(settings.github_client_id, settings.github_client_secret),
                json={"access_token": access_token},
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if response.status_code in {204, 404}:
                return True
            response.raise_for_status()
            return True
    except Exception as exc:
        # Never log the access token or a provider response body.
        logger.warning("GitHub token revocation failed (%s)", type(exc).__name__)
        return False


# -- Discord -----------------------------------------------------------------


@router.get("/login")
async def discord_login(request: Request):
    if not settings.discord_client_id:
        return _error_redirect("setup_required")

    state = _create_oauth_state(request, "discord")
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": DISCORD_SCOPES,
        "state": state,
        "prompt": "none",  # skip consent when the user has already authorized
        "integration_type": 1,  # user install, allowing a DM without a shared guild
    }
    return _no_store_redirect(
        f"https://discord.com/oauth2/authorize?{urlencode(params)}",
        status_code=302,
    )


@router.get("/callback")
async def discord_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    # State is checked even when the provider reports a denial.  The provider's
    # description is deliberately ignored so it can never be reflected to HTML.
    del error_description
    if _consume_oauth_state(request, state, "discord") is None:
        return _error_redirect("invalid_state")
    if error:
        return _error_redirect("oauth_denied" if error == "access_denied" else "oauth_failed")
    if not code:
        return _error_redirect("missing_code")
    if not settings.discord_client_id or not settings.discord_client_secret:
        return _error_redirect("setup_required")

    try:
        async with httpx.AsyncClient(timeout=settings.oauth_http_timeout) as client:
            token_resp = await client.post(
                f"{DISCORD_API}/oauth2/token",
                data={
                    "client_id": settings.discord_client_id,
                    "client_secret": settings.discord_client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.discord_redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
            if not isinstance(token_data, dict):
                return _error_redirect("oauth_failed")
            access_token = token_data.get("access_token")
            if (
                not isinstance(access_token, str)
                or not access_token
                or len(access_token) > 2048
            ):
                return _error_redirect("oauth_failed")

            user_resp = await client.get(
                f"{DISCORD_API}/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_resp.raise_for_status()
            discord_user = user_resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Discord OAuth request failed (%s)", type(exc).__name__)
        return _error_redirect("oauth_unavailable")

    if not isinstance(discord_user, dict):
        return _error_redirect("oauth_failed")
    raw_discord_id = discord_user.get("id")
    username = discord_user.get("username")
    discord_id = str(raw_discord_id) if isinstance(raw_discord_id, (str, int)) else ""
    if (
        not discord_id.isdigit()
        or len(discord_id) > 32
        or not isinstance(username, str)
        or not username
    ):
        return _error_redirect("oauth_failed")
    avatar = discord_user.get("avatar")
    if not isinstance(avatar, str):
        avatar = None

    db = SessionLocal()
    is_new_user = False
    user_id: int | None = None
    auth_session_id: str | None = None
    try:
        user = db.query(User).filter(User.discord_id == discord_id).first()
        if user is None:
            is_new_user = True
            user = User(discord_id=discord_id, username=username, avatar=avatar)
            db.add(user)
        else:
            user.username = username
            user.avatar = avatar
        db.flush()
        user_id = user.id
        auth_session_id = _create_auth_session(db, user_id)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Discord user persistence failed (%s)", type(exc).__name__)
        return _error_redirect("login_failed")
    finally:
        db.close()

    if user_id is None or auth_session_id is None:
        return _error_redirect("login_failed")

    # Drop any pre-login session data so authentication starts with a clean
    # session, then issue a fresh CSRF token for all state-changing forms.
    request.session.clear()
    request.session["user_id"] = user_id
    request.session[AUTH_SESSION_KEY] = auth_session_id
    ensure_csrf_token(request)

    if is_new_user:
        try:
            await send_dm(discord_id, build_welcome_message(username))
        except Exception as exc:
            # Signup remains usable when DMs are disabled or Discord is down.
            logger.warning("Welcome DM to %s failed (%s)", discord_id, type(exc).__name__)

    return _success_redirect()


# -- GitHub ------------------------------------------------------------------


@router.get("/github")
async def github_login(request: Request):
    """Start GitHub OAuth for the currently authenticated Discord user."""
    db = SessionLocal()
    try:
        user_id = authenticated_user_id(request, db)
    finally:
        db.close()
    if user_id is None:
        return _error_redirect("login_required")
    if not settings.github_client_id:
        return _error_redirect("github_setup_required")

    state = _create_oauth_state(request, "github", user_id=user_id)
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
        "scope": GITHUB_SCOPES,
        "state": state,
    }
    return _no_store_redirect(
        f"https://github.com/login/oauth/authorize?{urlencode(params)}",
        status_code=302,
    )


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    del error_description
    state_data = _consume_oauth_state(request, state, "github")
    if state_data is None:
        return _error_redirect("invalid_state")

    session_db = SessionLocal()
    try:
        session_user_id = authenticated_user_id(request, session_db)
    finally:
        session_db.close()
    state_user_id = state_data.get("uid")
    if (
        not isinstance(session_user_id, int)
        or isinstance(session_user_id, bool)
        or not isinstance(state_user_id, int)
        or isinstance(state_user_id, bool)
        or session_user_id != state_user_id
    ):
        return _error_redirect("invalid_state")
    if error:
        return _error_redirect("github_denied" if error == "access_denied" else "github_oauth_failed")
    if not code:
        return _error_redirect("missing_code")
    if not settings.github_client_id or not settings.github_client_secret:
        return _error_redirect("github_setup_required")

    try:
        async with httpx.AsyncClient(timeout=settings.oauth_http_timeout) as client:
            token_resp = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": settings.github_client_id,
                    "client_secret": settings.github_client_secret,
                    "code": code,
                    "redirect_uri": settings.github_redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
            if not isinstance(token_data, dict):
                return _error_redirect("github_token_failed")
            github_token = token_data.get("access_token")
            if (
                not isinstance(github_token, str)
                or not github_token
                or len(github_token) > 2048
            ):
                return _error_redirect("github_token_failed")

            gh_user_resp = await client.get(
                f"{GITHUB_API}/user",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            gh_user_resp.raise_for_status()
            gh_user = gh_user_resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("GitHub OAuth request failed (%s)", type(exc).__name__)
        return _error_redirect("github_unavailable")

    if not isinstance(gh_user, dict):
        return _error_redirect("github_oauth_failed")
    raw_github_id = gh_user.get("id")
    github_username = gh_user.get("login")
    github_id = str(raw_github_id) if isinstance(raw_github_id, (str, int)) else ""
    if (
        not github_id.isdigit()
        or len(github_id) > 32
        or not isinstance(github_username, str)
        or not github_username
        or len(github_username) > 100
    ):
        return _error_redirect("github_oauth_failed")

    old_token: str | None = None
    link_error: str | None = None
    clear_invalid_session = False
    db = SessionLocal()
    try:
        user = db.get(User, session_user_id)
        if user is None:
            link_error = "login_required"
            clear_invalid_session = True
        else:
            existing_owner = (
                db.query(User)
                .filter(User.github_id == github_id, User.id != session_user_id)
                .first()
            )
            if existing_owner is not None:
                link_error = "github_account_in_use"
            else:
                try:
                    old_token = user.github_token
                except TokenDecryptionError:
                    # A fresh OAuth grant is the recovery path for a damaged
                    # ciphertext or a corrected encryption key.
                    old_token = None
                user.github_id = github_id
                user.github_username = github_username
                user.github_token = github_token
                db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning("GitHub account link conflict (%s)", type(exc).__name__)
        link_error = "github_account_in_use"
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("GitHub account persistence failed (%s)", type(exc).__name__)
        link_error = "github_link_failed"
    finally:
        db.close()

    if link_error is not None:
        if clear_invalid_session:
            request.session.clear()
        await _revoke_github_token(github_token)
        return _error_redirect(link_error)

    ensure_csrf_token(request)
    if old_token and old_token != github_token:
        await _revoke_github_token(old_token)
    return _success_redirect(github="connected")


@router.post("/github/disconnect")
async def github_disconnect(request: Request):
    await require_csrf_token(request)

    github_token: str | None = None
    db = SessionLocal()
    try:
        user_id = authenticated_user_id(request, db)
        if user_id is None:
            return _error_redirect("login_required")
        user = db.get(User, user_id)
        if user is None:
            request.session.clear()
            return _error_redirect("login_required")
        try:
            github_token = user.github_token
        except TokenDecryptionError:
            github_token = None
        user.github_id = None
        user.github_username = None
        user.github_token = None
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("GitHub disconnect persistence failed (%s)", type(exc).__name__)
        return _error_redirect("github_disconnect_failed")
    finally:
        db.close()

    await _revoke_github_token(github_token)
    return _success_redirect(github="disconnected")


@router.get("/github/disconnect", include_in_schema=False)
async def github_disconnect_get():
    """Legacy URL remains non-mutating; callers must submit a CSRF POST."""
    return _method_not_allowed()


@router.post("/delete-account")
async def delete_account(request: Request):
    """Delete the authenticated user and cascading account data."""
    await require_csrf_token(request)

    github_token: str | None = None
    db = SessionLocal()
    try:
        user_id = authenticated_user_id(request, db)
        if user_id is None:
            return _error_redirect("login_required")
        user = db.get(User, user_id)
        if user is None:
            request.session.clear()
            return _error_redirect("login_required")
        try:
            github_token = user.github_token
        except TokenDecryptionError:
            github_token = None
        db.delete(user)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Account deletion failed (%s)", type(exc).__name__)
        return _error_redirect("account_delete_failed")
    finally:
        db.close()

    request.session.clear()
    await _revoke_github_token(github_token)
    return _success_redirect(account_deleted="1")


@router.get("/delete-account", include_in_schema=False)
async def delete_account_get():
    return _method_not_allowed()


@router.post("/logout")
async def logout(request: Request):
    if request.session.get("user_id") is None:
        request.session.clear()
        return _success_redirect()
    await require_csrf_token(request)
    db = SessionLocal()
    try:
        user_id = authenticated_user_id(request, db)
        session_id = request.session.get(AUTH_SESSION_KEY)
        if user_id is not None and isinstance(session_id, str):
            db.query(AuthSession).filter(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
            ).delete(synchronize_session=False)
            db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Logout session revocation failed (%s)", type(exc).__name__)
        return _error_redirect("logout_failed")
    finally:
        db.close()
    request.session.clear()
    return _success_redirect()


@router.get("/logout", include_in_schema=False)
async def logout_get():
    """A GET must never log a user out (e.g. through a third-party image URL)."""
    return _method_not_allowed()
