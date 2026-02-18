"""Discord OAuth2 login / logout flow."""

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
SCOPES = "identify"
STATE_MAX_AGE = 600  # 10 minutes


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="oauth2-state")


# ─── Routes ────────────────────────────────────────────────────────────────────

@router.get("/login")
async def login(request: Request):
    """Redirect the user to Discord's OAuth2 consent screen."""
    if not settings.discord_client_id:
        return RedirectResponse("/?error=setup_required")

    # Sign a timestamp as the state — no session needed
    state = _signer().dumps({"ts": time.time()})

    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    query = urlencode(params)
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{query}")


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    """Handle Discord's redirect back with an authorization code."""
    # Verify the signed state (replaces session-based CSRF check)
    try:
        _signer().loads(state, max_age=STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return RedirectResponse("/?error=invalid_state")

    async with httpx.AsyncClient() as client:
        # Exchange code for access token
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
        access_token = token_data["access_token"]

        # Fetch Discord user info
        user_resp = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_resp.raise_for_status()
        discord_user = user_resp.json()

    # Upsert user in DB
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


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")
