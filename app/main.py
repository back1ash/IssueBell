"""IssueBell — FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.database import SessionLocal, engine
from app.models import (
    Base,
    NotificationDelivery,
    RepositoryPollState,
    Subscription,
    User,
)
from app.routers import admin, auth, legal, subscriptions
from app.services.discord import DiscordDeliveryError, send_dm
from app.services.github import GitHubAPIError, build_issue_message, fetch_new_issues, match_label
from app.token_crypto import TokenDecryptionError, migrate_plaintext_tokens

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

# New reliability tables are additive, so create_all remains compatible with
# existing SQLite and PostgreSQL installations. Existing columns are unchanged.
Base.metadata.create_all(bind=engine)
migrate_plaintext_tokens(engine)

DELIVERY_LEASE_SECONDS = 300
DELIVERY_BATCH_SIZE = 20
DELIVERY_OPERATION_TIMEOUT_SECONDS = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_github_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _load_or_create_poll_state(
    db: Session, user_id: int, repo: str
) -> RepositoryPollState:
    state = db.query(RepositoryPollState).filter(
        RepositoryPollState.user_id == user_id,
        RepositoryPollState.repo_full_name == repo,
    ).first()
    if state is not None:
        return state

    state = RepositoryPollState(user_id=user_id, repo_full_name=repo)
    try:
        with db.begin_nested():
            db.add(state)
            db.flush()
    except IntegrityError:
        state = db.query(RepositoryPollState).filter(
            RepositoryPollState.user_id == user_id,
            RepositoryPollState.repo_full_name == repo,
        ).one()
    return state


def _record_poll_failure(
    db: Session,
    *,
    user_id: int,
    repo: str,
    attempted_at: datetime,
    code: str,
    message: str,
    rate_limit_reset_at: datetime | None = None,
) -> None:
    state = _load_or_create_poll_state(db, user_id, repo)
    state.last_attempt_at = attempted_at
    state.error_code = code
    state.error_message = message[:1000]
    state.rate_limit_reset_at = rate_limit_reset_at
    db.commit()


def _record_poll_success(
    db: Session,
    *,
    user_id: int,
    repo: str,
    checked_at: datetime,
    subscriptions_for_repo: list[Subscription],
) -> None:
    state = _load_or_create_poll_state(db, user_id, repo)
    state.last_attempt_at = checked_at
    state.last_success_at = checked_at
    state.error_code = None
    state.error_message = None
    state.rate_limit_reset_at = None
    for subscription in subscriptions_for_repo:
        subscription.last_checked_at = checked_at
    db.commit()


def _enqueue_issue_delivery(
    db: Session,
    *,
    user_id: int,
    repo: str,
    issue: dict[str, Any],
    matched_label: str,
) -> NotificationDelivery:
    issue_id = str(issue.get("id") or issue.get("node_id") or issue.get("number"))
    existing = db.query(NotificationDelivery).filter(
        NotificationDelivery.user_id == user_id,
        NotificationDelivery.repo_full_name == repo,
        NotificationDelivery.issue_id == issue_id,
    ).first()
    if existing is not None:
        return existing

    issue_number = issue.get("number")
    delivery = NotificationDelivery(
        user_id=user_id,
        delivery_type="issue",
        repo_full_name=repo,
        issue_id=issue_id,
        issue_number=issue_number if isinstance(issue_number, int) else None,
        matched_label=matched_label,
        message=build_issue_message(issue, repo, matched_label),
        status="pending",
        next_attempt_at=_utcnow(),
    )
    try:
        with db.begin_nested():
            db.add(delivery)
            db.flush()
    except IntegrityError:
        # Another scheduler instance won the race. The unique constraint is the
        # durable idempotency boundary shared by SQLite/PostgreSQL workers.
        return db.query(NotificationDelivery).filter(
            NotificationDelivery.user_id == user_id,
            NotificationDelivery.repo_full_name == repo,
            NotificationDelivery.issue_id == issue_id,
        ).one()
    db.commit()
    return delivery


def _retry_delay(attempt_count: int) -> timedelta:
    seconds = min(3600, 30 * (2 ** min(max(attempt_count - 1, 0), 7)))
    return timedelta(seconds=seconds)


async def _deliver_due_notifications(db: Session) -> None:
    """Claim and deliver a bounded batch from the durable outbox."""

    now = _utcnow()
    claimable = or_(
        NotificationDelivery.status.in_(("pending", "failed")),
        and_(
            NotificationDelivery.status == "sending",
            or_(
                NotificationDelivery.lease_until.is_(None),
                NotificationDelivery.lease_until <= now,
            ),
        ),
    )
    due_ids = [
        row[0]
        for row in db.query(NotificationDelivery.id).filter(
            NotificationDelivery.delivery_type == "issue",
            NotificationDelivery.sent_at.is_(None),
            claimable,
            or_(
                NotificationDelivery.next_attempt_at.is_(None),
                NotificationDelivery.next_attempt_at <= now,
            ),
        ).order_by(NotificationDelivery.created_at).limit(DELIVERY_BATCH_SIZE).all()
    ]

    for delivery_id in due_ids:
        claim_time = _utcnow()
        updated = db.query(NotificationDelivery).filter(
            NotificationDelivery.id == delivery_id,
            NotificationDelivery.delivery_type == "issue",
            NotificationDelivery.sent_at.is_(None),
            or_(
                NotificationDelivery.status.in_(("pending", "failed")),
                and_(
                    NotificationDelivery.status == "sending",
                    or_(
                        NotificationDelivery.lease_until.is_(None),
                        NotificationDelivery.lease_until <= claim_time,
                    ),
                ),
            ),
            or_(
                NotificationDelivery.next_attempt_at.is_(None),
                NotificationDelivery.next_attempt_at <= claim_time,
            ),
        ).update(
            {
                NotificationDelivery.status: "sending",
                NotificationDelivery.lease_until: claim_time
                + timedelta(seconds=DELIVERY_LEASE_SECONDS),
                NotificationDelivery.last_attempt_at: claim_time,
                NotificationDelivery.attempt_count: NotificationDelivery.attempt_count + 1,
            },
            synchronize_session=False,
        )
        db.commit()
        if updated != 1:
            continue

        db.expire_all()
        delivery = db.get(NotificationDelivery, delivery_id)
        if delivery is None:
            continue
        user_identity = db.query(User.id, User.discord_id).filter(
            User.id == delivery.user_id
        ).one_or_none()
        if user_identity is None:
            continue

        try:
            await asyncio.wait_for(
                send_dm(user_identity.discord_id, delivery.message),
                timeout=DELIVERY_OPERATION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            failed_at = _utcnow()
            delivery.last_error = str(exc)[:1000]
            delivery.lease_until = None
            is_permanent = (
                isinstance(exc, DiscordDeliveryError) and not exc.retryable
            )
            if is_permanent:
                delivery.status = "dead"
                delivery.next_attempt_at = None
            else:
                delivery.status = "failed"
                delivery.next_attempt_at = failed_at + _retry_delay(
                    delivery.attempt_count
                )
            logger.warning(
                "DM delivery %s to user %s %s (attempt=%s): %s",
                delivery.id,
                user_identity.id,
                "stopped" if is_permanent else "failed",
                delivery.attempt_count,
                exc,
            )
        else:
            delivery.status = "sent"
            delivery.sent_at = _utcnow()
            delivery.last_error = None
            delivery.lease_until = None
            delivery.next_attempt_at = None
        db.commit()


async def poll_all_users() -> None:
    """Check updates and durably enqueue matching issue notifications."""

    db: Session = SessionLocal()
    try:
        user_ids = [
            row[0]
            for row in db.query(User.id).filter(User.github_token.isnot(None)).all()
        ]
        for user_id in user_ids:
            repo_map: dict[str, list[Subscription]] = defaultdict(list)
            subscriptions = db.query(Subscription).filter(
                Subscription.user_id == user_id
            ).all()
            for subscription in subscriptions:
                repo_map[subscription.repo_full_name].append(subscription)

            try:
                github_token = db.query(User.github_token).filter(
                    User.id == user_id
                ).scalar()
            except TokenDecryptionError as exc:
                db.rollback()
                attempted_at = _utcnow()
                for repo in repo_map:
                    _record_poll_failure(
                        db,
                        user_id=user_id,
                        repo=repo,
                        attempted_at=attempted_at,
                        code="token_decryption_error",
                        message=str(exc),
                    )
                logger.error(
                    "Skipping GitHub polling for user %s: stored token cannot be decrypted",
                    user_id,
                )
                continue
            if not github_token:
                continue

            for repo, repo_subscriptions in repo_map.items():
                poll_started_at = _utcnow()
                previous_state = db.query(RepositoryPollState).filter(
                    RepositoryPollState.user_id == user_id,
                    RepositoryPollState.repo_full_name == repo,
                ).first()
                if (
                    previous_state is not None
                    and previous_state.error_code == "github_rate_limit"
                    and previous_state.rate_limit_reset_at is not None
                    and previous_state.rate_limit_reset_at > poll_started_at
                ):
                    continue
                cursor_ats = [
                    subscription.last_checked_at or subscription.created_at
                    for subscription in repo_subscriptions
                    if subscription.last_checked_at or subscription.created_at
                ]
                since = min(cursor_ats) if cursor_ats else (
                    poll_started_at - timedelta(seconds=settings.poll_interval)
                )

                try:
                    issues = await fetch_new_issues(repo, github_token, since)
                    for issue in issues:
                        updated_at = _parse_github_timestamp(
                            issue.get("updated_at") or issue.get("created_at")
                        )
                        # GitHub timestamps have one-second precision. Floor each
                        # checkpoint to avoid skipping an update in the same second.
                        eligible_subscriptions = [
                            subscription
                            for subscription in repo_subscriptions
                            if subscription.last_checked_at is None
                            or updated_at is None
                            or updated_at
                            >= subscription.last_checked_at.replace(microsecond=0)
                        ]
                        issue_labels = [
                            str(label.get("name", ""))
                            for label in issue.get("labels", [])
                            if isinstance(label, dict)
                        ]
                        first_match = next(
                            (
                                (subscription, matched)
                                for subscription in eligible_subscriptions
                                if (
                                    matched := match_label(subscription.label, issue_labels)
                                )
                                is not None
                            ),
                            None,
                        )
                        if first_match is not None:
                            _, matched_label = first_match
                            _enqueue_issue_delivery(
                                db,
                                user_id=user_id,
                                repo=repo,
                                issue=issue,
                                matched_label=matched_label,
                            )
                except GitHubAPIError as exc:
                    _record_poll_failure(
                        db,
                        user_id=user_id,
                        repo=repo,
                        attempted_at=poll_started_at,
                        code=exc.code,
                        message=str(exc),
                        rate_limit_reset_at=exc.rate_limit_reset_at,
                    )
                    logger.warning("Polling %s failed (%s): %s", repo, exc.code, exc)
                    continue
                except Exception as exc:
                    _record_poll_failure(
                        db,
                        user_id=user_id,
                        repo=repo,
                        attempted_at=poll_started_at,
                        code="poll_processing_error",
                        message=str(exc),
                    )
                    logger.exception("Polling %s failed while processing results", repo)
                    continue

                # Advance only after every page was fetched and every matching
                # issue was safely inserted into the durable outbox.
                _record_poll_success(
                    db,
                    user_id=user_id,
                    repo=repo,
                    checked_at=poll_started_at,
                    subscriptions_for_repo=repo_subscriptions,
                )

    except Exception as exc:
        db.rollback()
        logger.error("poll_all_users crashed: %s", exc, exc_info=True)
    finally:
        db.close()


async def deliver_notifications() -> None:
    """Drain a bounded outbox batch independently from GitHub polling."""

    db: Session = SessionLocal()
    try:
        await _deliver_due_notifications(db)
    except Exception as exc:
        db.rollback()
        logger.error("deliver_notifications crashed: %s", exc, exc_info=True)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_all_users,
        "interval",
        seconds=settings.poll_interval,
        id="poll_all_users",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        deliver_notifications,
        "interval",
        seconds=max(5, min(30, settings.poll_interval)),
        id="deliver_notifications",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Scheduler started (interval=%ss)", settings.poll_interval)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


app = FastAPI(title="IssueBell", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="issuebell_session",
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=settings.secure_cookies,
)
allowed_hosts = [host.strip() for host in settings.allowed_hosts.split(",") if host.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["localhost"])


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "object-src 'none'; form-action 'self'; connect-src 'self'; "
        "img-src 'self' data: https://cdn.discordapp.com; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
    )
    if settings.secure_cookies:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    private_paths = (
        "/auth",
        "/subscriptions",
        "/admin",
        "/manage",
        "/events",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
        "/healthz",
        "/readyz",
    )
    if request.url.path.startswith(private_paths):
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    return response

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth.router)
app.include_router(subscriptions.router)
app.include_router(subscriptions.events_router)
app.include_router(admin.router)
app.include_router(legal.router)


@app.get("/healthz", include_in_schema=False)
@app.get("/health/live", include_in_schema=False)
def health_live():
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
@app.get("/health/ready", include_in_schema=False)
def health_ready():
    db: Session = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Readiness database check failed")
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    finally:
        db.close()
    return {"status": "ready"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = None
    user_subscriptions: list[Subscription] = []
    github_connected = False
    github_reconnect_required = False

    if request.session.get("user_id") is not None:
        db: Session = SessionLocal()
        try:
            user_id = auth.authenticated_user_id(request, db)
            user = db.get(User, user_id) if user_id is not None else None
            if user:
                github_connected = user.github_id is not None
                user_subscriptions = (
                    db.query(Subscription)
                    .filter(Subscription.user_id == user.id)
                    .order_by(Subscription.created_at.desc())
                    .all()
                )
                github_reconnect_required = db.query(RepositoryPollState.id).filter(
                    RepositoryPollState.user_id == user.id,
                    RepositoryPollState.error_code.in_(
                        (
                            "github_authentication_error",
                            "github_forbidden",
                            "token_decryption_error",
                        )
                    ),
                ).first() is not None
            else:
                request.session.clear()
        finally:
            db.close()

    csrf_token = auth.ensure_csrf_token(request) if user else ""

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "user": user,
            "subscriptions": user_subscriptions,
            "github_connected": github_connected,
            "github_reconnect_required": github_reconnect_required,
            "csrf_token": csrf_token,
        },
    )


@app.get("/manage", response_class=HTMLResponse)
async def manage(request: Request):
    db: Session = SessionLocal()
    try:
        user_id = auth.authenticated_user_id(request, db)
        if user_id is None:
            return RedirectResponse(url="/")
        user = db.get(User, user_id)
        if user is None or user.discord_id != settings.admin_discord_id:
            return RedirectResponse(url="/")
    finally:
        db.close()
    return templates.TemplateResponse(
        request=request,
        name="manage.html",
        context={
            "request": request,
            "user": user,
            "csrf_token": auth.ensure_csrf_token(request),
        },
    )
