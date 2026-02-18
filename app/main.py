"""IssueBell — FastAPI application entry point."""

import logging

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

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

# Create tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="IssueBell", version="0.1.0")

# Session middleware (cookie-based, signed with secret_key)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# Static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Routers
app.include_router(auth.router)
app.include_router(subscriptions.router)
app.include_router(webhook.router)


# ─── Web UI ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user_id = request.session.get("user_id")
    user = None
    subs = []

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
