"""IssueBell — FastAPI application entry point."""

import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import SessionLocal, engine
from app.models import Base, Subscription, User
from app.routers import auth, subscriptions, webhook
from app.services.discord import send_dm
from app.services.github import build_issue_message, fetch_new_issues, match_label

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

# ── Schema bootstrap ─────────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)


# ── Background polling job ───────────────────────────────────────────────────

async def poll_all_users() -> None:
    """Check every user's subscriptions for new matching issues."""
    db: Session = SessionLocal()
    try:
        users = db.query(User).filter(User.github_token.isnot(None)).all()
        for user in users:
            # Group subscriptions by repo — 1 API call per unique repo per user.
            repo_map: dict[str, list[Subscription]] = defaultdict(list)
            for sub in user.subscriptions:
                repo_map[sub.repo_full_name].append(sub)

            for repo, subs in repo_map.items():
                checked_ats = [s.last_checked_at for s in subs if s.last_checked_at]
                since = min(checked_ats) if checked_ats else None

                try:
                    issues = await fetch_new_issues(repo, user.github_token, since)
                except Exception as exc:
                    logger.warning("Polling %s failed: %s", repo, exc)
                    continue

                now = datetime.now(timezone.utc).replace(tzinfo=None)
                for issue in issues:
                    issue_labels = [lb["name"] for lb in issue.get("labels", [])]
                    for sub in subs:
                        matched = match_label(sub.label, issue_labels)
                        if matched:
                            try:
                                await send_dm(
                                    user.discord_id,
                                    build_issue_message(issue, repo, matched),
                                )
                            except Exception as exc:
                                logger.warning("DM to %s failed: %s", user.discord_id, exc)

                for sub in subs:
                    sub.last_checked_at = now
                db.commit()
    except Exception as exc:
        logger.error("poll_all_users crashed: %s", exc, exc_info=True)
    finally:
        db.close()


# ── App lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_all_users,
        "interval",
        seconds=settings.poll_interval,
        id="poll_all_users",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started (interval=%ss)", settings.poll_interval)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(title="IssueBell", version="0.2.0", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth.router)
app.include_router(subscriptions.router)
app.include_router(webhook.router)


# ── Web UI ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user_id = request.session.get("user_id")
    user = None
    subs: list[Subscription] = []

    if user_id:
        db: Session = SessionLocal()
        try:
            user = db.get(User, user_id)
            if user:
                subs = (
                    db.query(Subscription)
                    .filter(Subscription.user_id == user.id)
                    .order_by(Subscription.created_at.desc())
                    .all()
                )
        finally:
            db.close()

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "user": user, "subscriptions": subs},
    )
