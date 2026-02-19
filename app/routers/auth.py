"""Discord + GitHub OAuth2 login / logout flow."""

import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings
from app.database import SessionLocal
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

DISCORD_API = "https://discord.com/api/v10"
GITHUB_API  = "https://api.github.com"
DISCORD_SCOPES = "identify applications.commands"
GITHUB_SCOPES  = "read:user"       # only needed for profile; public repo access needs no scope
STATE_MAX_AGE  = 600               # 10 minutes


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="oauth2-state")


# ── Discord ─────────────────────────────────────────────────────────────────────

@router.get("/login")
async def discord_login(request: Request):
    if not settings.discord_client_id:
        return RedirectResponse("/?error=setup_required")

    state = _signer().dumps({"ts": time.time(), "provider": "discord"})
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": DISCORD_SCOPES,
        "state": state,
        "prompt": "none",       # skip consent screen if already authorized
        "integration_type": 1,  # 0 = guild install, 1 = user install (allows DM without shared server)
    }
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{urlencode(params)}")


@router.get("/callback")
async def discord_callback(request: Request, code: str = "", state: str = ""):
    try:
        _signer().loads(state, max_age=STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return RedirectResponse("/?error=invalid_state")

    async with httpx.AsyncClient() as client:
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
        access_token = token_resp.json()["access_token"]

        user_resp = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_resp.raise_for_status()
        discord_user = user_resp.json()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.discord_id == discord_user["id"]).first()
        if user is None:
            user = User(
                discord_id=discord_user["id"],
                username=discord_user["username"],
                avatar=discord_user.get("avatar"),
            )
            db.add(user)
        else:
            user.username = discord_user["username"]
            user.avatar = discord_user.get("avatar")
        db.commit()
        db.refresh(user)
    finally:
        db.close()

    request.session["user_id"] = user.id
    return RedirectResponse("/")


# ── GitHub ──────────────────────────────────────────────────────────────────────

@router.get("/github")
async def github_login(request: Request):
    """Redirect to GitHub OAuth consent screen (must already be logged in with Discord)."""
    if not request.session.get("user_id"):
        return RedirectResponse("/")
    if not settings.github_client_id:
        return RedirectResponse("/?error=github_setup_required")

    state = _signer().dumps({"ts": time.time(), "provider": "github",
                              "uid": request.session["user_id"]})
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
        "scope": GITHUB_SCOPES,
        "state": state,
    }
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{urlencode(params)}")


@router.get("/github/callback")
async def github_callback(request: Request, code: str = "", state: str = ""):
    try:
        data = _signer().loads(state, max_age=STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return RedirectResponse("/?error=invalid_state")

    user_id = data.get("uid") or request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/")

    async with httpx.AsyncClient() as client:
        # Exchange code for token
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
        github_token = token_data.get("access_token")
        if not github_token:
            return RedirectResponse("/?error=github_token_failed")

        # Fetch GitHub user info
        gh_user_resp = await client.get(
            f"{GITHUB_API}/user",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        gh_user_resp.raise_for_status()
        gh_user = gh_user_resp.json()

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user:
            user.github_id = str(gh_user["id"])
            user.github_username = gh_user["login"]
            user.github_token = github_token
            db.commit()
    finally:
        db.close()

    return RedirectResponse("/")


@router.get("/github/disconnect")
async def github_disconnect(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/")
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user:
            user.github_id = None
            user.github_username = None
            user.github_token = None
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")
