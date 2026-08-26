"""Subscription, repository discovery, delivery history, and test-DM APIs."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    NotificationDelivery,
    ProductEvent,
    RepositoryPollState,
    Subscription,
    User,
    UserActionThrottle,
)
from app.schemas import (
    NotificationHistoryRead,
    ProductEventCreate,
    RepositoryLabelsRead,
    SubscriptionCreate,
    SubscriptionRead,
    SubscriptionStatusResponse,
    TestDMRead,
)
from app.routers.auth import authenticated_user_id, require_csrf_token
from app.services.discord import send_dm
from app.services.github import (
    GitHubAPIError,
    GitHubLabelNotFoundError,
    GitHubRateLimitError,
    fetch_repository_labels,
    validate_repository_label,
)
from app.token_crypto import TokenDecryptionError

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])
events_router = APIRouter(tags=["events"])

MAX_SUBSCRIPTIONS_PER_USER = 100
MAX_LABELS_IN_LOOKUP_RESPONSE = 200
REPOSITORY_LOOKUP_COOLDOWN_SECONDS = 2
TEST_DM_COOLDOWN_SECONDS = 30
SUBSCRIPTION_CREATE_COOLDOWN_SECONDS = 1
MAX_TEST_DELIVERIES_PER_USER = 20
_REPO_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = authenticated_user_id(request, db)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _github_token_or_reconnect(user: User) -> str:
    try:
        token = user.github_token
    except TokenDecryptionError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "github_reconnect_required",
                "message": "Reconnect GitHub to replace an unreadable authorization token",
            },
        ) from exc
    if not token:
        raise HTTPException(
            status_code=409,
            detail={"code": "github_not_connected", "message": "Connect GitHub first"},
        )
    return token


def _consume_cooldown(
    db: Session,
    *,
    user_id: int,
    action: str,
    cooldown_seconds: int,
) -> int:
    """Atomically consume a persistent cooldown; return retry seconds or zero."""

    now = _utcnow()
    cutoff = now - timedelta(seconds=cooldown_seconds)
    updated = db.query(UserActionThrottle).filter(
        UserActionThrottle.user_id == user_id,
        UserActionThrottle.action == action,
        UserActionThrottle.last_used_at <= cutoff,
    ).update(
        {UserActionThrottle.last_used_at: now},
        synchronize_session=False,
    )
    if updated == 1:
        db.commit()
        return 0

    throttle = db.query(UserActionThrottle).filter(
        UserActionThrottle.user_id == user_id,
        UserActionThrottle.action == action,
    ).first()
    if throttle is not None:
        elapsed = max(0.0, (now - throttle.last_used_at).total_seconds())
        db.rollback()
        return max(1, math.ceil(cooldown_seconds - elapsed))

    try:
        db.add(UserActionThrottle(user_id=user_id, action=action, last_used_at=now))
        db.commit()
        return 0
    except IntegrityError:
        # A concurrent request created the throttle first.
        db.rollback()
        return cooldown_seconds


def _require_cooldown(
    db: Session,
    *,
    user_id: int,
    action: str,
    cooldown_seconds: int,
) -> None:
    retry_after = _consume_cooldown(
        db,
        user_id=user_id,
        action=action,
        cooldown_seconds=cooldown_seconds,
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": f"Try again in {retry_after} seconds",
            },
            headers={"Retry-After": str(retry_after)},
        )


def _record_product_event_once(db: Session, user_id: int, event_name: str) -> None:
    exists = db.query(ProductEvent.id).filter(
        ProductEvent.user_id == user_id,
        ProductEvent.event_name == event_name,
    ).first()
    if exists is None:
        db.add(ProductEvent(user_id=user_id, event_name=event_name))


def _prune_test_deliveries(db: Session, user_id: int) -> None:
    stale_ids = [
        row[0]
        for row in db.query(NotificationDelivery.id).filter(
            NotificationDelivery.user_id == user_id,
            NotificationDelivery.delivery_type == "test",
        ).order_by(NotificationDelivery.created_at.desc(), NotificationDelivery.id.desc()).offset(
            MAX_TEST_DELIVERIES_PER_USER
        ).all()
    ]
    if stale_ids:
        db.query(NotificationDelivery).filter(
            NotificationDelivery.id.in_(stale_ids)
        ).delete(synchronize_session=False)


def _raise_github_http(exc: GitHubAPIError) -> None:
    detail: dict[str, object] = {"code": exc.code, "message": str(exc)}
    headers: dict[str, str] | None = None
    if isinstance(exc, GitHubLabelNotFoundError):
        detail["available_labels"] = exc.labels[:50]
    if isinstance(exc, GitHubRateLimitError):
        retry_after = 60
        if exc.rate_limit_reset_at:
            retry_after = max(1, math.ceil((exc.rate_limit_reset_at - _utcnow()).total_seconds()))
        headers = {"Retry-After": str(retry_after)}
    raise HTTPException(status_code=exc.http_status, detail=detail, headers=headers)


@router.get("/", response_model=list[SubscriptionRead])
def list_subscriptions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(Subscription)
        .filter(Subscription.user_id == current_user.id)
        .order_by(Subscription.created_at.desc())
        .all()
    )


@router.get("/status", response_model=SubscriptionStatusResponse)
def subscription_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    subscriptions = (
        db.query(Subscription)
        .filter(Subscription.user_id == current_user.id)
        .order_by(Subscription.created_at.desc())
        .all()
    )
    states = {
        state.repo_full_name: state
        for state in db.query(RepositoryPollState).filter(
            RepositoryPollState.user_id == current_user.id
        ).all()
    }

    summaries: dict[str, dict[str, object]] = defaultdict_delivery_summary()
    delivery_rows = db.query(
        NotificationDelivery.repo_full_name,
        NotificationDelivery.status,
        func.count(NotificationDelivery.id),
        func.max(NotificationDelivery.sent_at),
    ).filter(
        NotificationDelivery.user_id == current_user.id,
        NotificationDelivery.delivery_type == "issue",
    ).group_by(
        NotificationDelivery.repo_full_name,
        NotificationDelivery.status,
    ).all()
    for repo, status, count, last_sent_at in delivery_rows:
        summary = summaries.setdefault(
            repo,
            {
                "sent_count": 0,
                "pending_count": 0,
                "failed_count": 0,
                "dead_count": 0,
                "cancelled_count": 0,
                "last_sent_at": None,
            },
        )
        if status == "sent":
            summary["sent_count"] = int(summary["sent_count"]) + count
            if last_sent_at and (
                summary["last_sent_at"] is None or last_sent_at > summary["last_sent_at"]
            ):
                summary["last_sent_at"] = last_sent_at
        elif status == "dead":
            summary["dead_count"] = int(summary["dead_count"]) + count
        elif status == "failed":
            summary["failed_count"] = int(summary["failed_count"]) + count
        elif status == "cancelled":
            summary["cancelled_count"] = int(summary["cancelled_count"]) + count
        else:
            summary["pending_count"] = int(summary["pending_count"]) + count

    result = []
    for subscription in subscriptions:
        state = states.get(subscription.repo_full_name)
        result.append(
            {
                "id": subscription.id,
                "user_id": subscription.user_id,
                "repo_full_name": subscription.repo_full_name,
                "label": subscription.label,
                "last_checked_at": subscription.last_checked_at,
                "created_at": subscription.created_at,
                "poll": None
                if state is None
                else {
                    "last_attempt_at": state.last_attempt_at,
                    "last_success_at": state.last_success_at,
                    "error_code": state.error_code,
                    "error_message": state.error_message,
                    "rate_limit_reset_at": state.rate_limit_reset_at,
                    "last_examined_count": state.last_examined_count,
                    "last_actionable_count": state.last_actionable_count,
                    "last_ignored_update_count": state.last_ignored_update_count,
                    "last_event_failure_count": state.last_event_failure_count,
                },
                "delivery": summaries.get(
                    subscription.repo_full_name,
                    {
                        "sent_count": 0,
                        "pending_count": 0,
                        "failed_count": 0,
                        "dead_count": 0,
                        "cancelled_count": 0,
                        "last_sent_at": None,
                    },
                ),
            }
        )
    return {
        "github_connected": current_user.github_id is not None,
        "subscriptions": result,
    }


def defaultdict_delivery_summary() -> dict[str, dict[str, object]]:
    # Kept as a helper so status assembly remains straightforward to unit test.
    return {}


@router.get("/history", response_model=list[NotificationHistoryRead])
def notification_history(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    deliveries = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.user_id == current_user.id)
        .order_by(NotificationDelivery.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": delivery.id,
            "delivery_type": delivery.delivery_type,
            "repo_full_name": delivery.repo_full_name,
            "issue_id": delivery.source_issue_id or delivery.issue_id.split(":", 1)[0],
            "issue_number": delivery.issue_number,
            "matched_label": delivery.matched_label,
            "trigger_type": delivery.trigger_type,
            "trigger_event_id": delivery.trigger_event_id,
            "status": delivery.status,
            "attempt_count": delivery.attempt_count,
            "last_error": delivery.last_error,
            "last_attempt_at": delivery.last_attempt_at,
            "sent_at": delivery.sent_at,
            "created_at": delivery.created_at,
        }
        for delivery in deliveries
    ]


@router.get(
    "/repositories/{owner}/{repo_name}/labels",
    response_model=RepositoryLabelsRead,
)
async def repository_labels(
    owner: str,
    repo_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _REPO_PART.fullmatch(owner) or not _REPO_PART.fullmatch(repo_name):
        raise HTTPException(status_code=422, detail="Invalid GitHub repository name")
    github_token = _github_token_or_reconnect(current_user)

    _require_cooldown(
        db,
        user_id=current_user.id,
        action="repository_label_lookup",
        cooldown_seconds=REPOSITORY_LOOKUP_COOLDOWN_SECONDS,
    )
    repo_full_name = f"{owner}/{repo_name}".lower()
    try:
        repository, labels = await fetch_repository_labels(
            repo_full_name, github_token
        )
    except GitHubAPIError as exc:
        _raise_github_http(exc)

    safe_labels = [
        {
            "name": str(label.get("name", "")),
            "color": str(label.get("color", "")),
            "description": label.get("description"),
        }
        for label in labels[:MAX_LABELS_IN_LOOKUP_RESPONSE]
    ]
    return {
        "repo_full_name": str(repository.get("full_name", repo_full_name)),
        "html_url": str(repository.get("html_url", f"https://github.com/{repo_full_name}")),
        "description": repository.get("description"),
        "private": bool(repository.get("private", False)),
        "labels": safe_labels,
    }


@router.post("/", response_model=SubscriptionRead, status_code=201)
async def create_subscription(
    payload: SubscriptionCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await require_csrf_token(request)
    _require_cooldown(
        db,
        user_id=current_user.id,
        action="subscription_create",
        cooldown_seconds=SUBSCRIPTION_CREATE_COOLDOWN_SECONDS,
    )
    duplicate = db.query(Subscription.id).filter(
        Subscription.user_id == current_user.id,
        Subscription.repo_full_name == payload.repo_full_name,
        Subscription.label == payload.label,
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="You already have a subscription for this repo + label combination.",
        )
    subscription_count = db.query(func.count(Subscription.id)).filter(
        Subscription.user_id == current_user.id
    ).scalar() or 0
    if subscription_count >= MAX_SUBSCRIPTIONS_PER_USER:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "subscription_limit_reached",
                "message": f"A maximum of {MAX_SUBSCRIPTIONS_PER_USER} subscriptions is allowed",
            },
        )
    github_token = _github_token_or_reconnect(current_user)

    try:
        await validate_repository_label(
            payload.repo_full_name,
            payload.label,
            github_token,
        )
    except GitHubAPIError as exc:
        _raise_github_http(exc)

    subscription = Subscription(
        user_id=current_user.id,
        repo_full_name=payload.repo_full_name,
        label=payload.label,
        last_checked_at=None,
    )
    db.add(subscription)
    _record_product_event_once(db, current_user.id, "subscription_created")
    try:
        db.commit()
        db.refresh(subscription)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="You already have a subscription for this repo + label combination.",
        )
    return subscription


@router.post("/test-dm", response_model=TestDMRead)
async def test_dm(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await require_csrf_token(request)
    _require_cooldown(
        db,
        user_id=current_user.id,
        action="test_dm",
        cooldown_seconds=TEST_DM_COOLDOWN_SECONDS,
    )
    attempted_at = _utcnow()
    test_event_id = str(uuid4())
    delivery = NotificationDelivery(
        user_id=current_user.id,
        delivery_type="test",
        repo_full_name="__test__",
        issue_id=f"test:{test_event_id}",
        source_issue_id="test",
        trigger_type="test",
        trigger_event_id=test_event_id,
        issue_number=None,
        matched_label=None,
        message=(
            "\U0001f514 **IssueBell test notification**\n"
            "Your Discord connection is working. Future matching GitHub issues will appear here."
        ),
        status="sending",
        attempt_count=1,
        last_attempt_at=attempted_at,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    _prune_test_deliveries(db, current_user.id)
    db.commit()

    try:
        await send_dm(current_user.discord_id, delivery.message)
    except Exception as exc:
        delivery.status = "failed"
        delivery.last_error = str(exc)[:1000]
        db.commit()
        raise HTTPException(
            status_code=502,
            detail={"code": "discord_delivery_failed", "message": "Test DM could not be sent"},
        )

    sent_at = _utcnow()
    delivery.status = "sent"
    delivery.sent_at = sent_at
    delivery.last_error = None
    _record_product_event_once(db, current_user.id, "test_dm_sent")
    db.commit()
    return {"status": "sent", "sent_at": sent_at}


@router.delete("/{subscription_id}", status_code=204)
async def delete_subscription(
    subscription_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await require_csrf_token(request)
    subscription = db.query(Subscription).filter(
        Subscription.id == subscription_id,
        Subscription.user_id == current_user.id,
    ).first()
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    db.delete(subscription)
    _record_product_event_once(db, current_user.id, "subscription_deleted")
    db.commit()
    return Response(status_code=204)


@events_router.post("/events", status_code=204)
async def record_product_event(
    payload: ProductEventCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await require_csrf_token(request)
    # Payload-free, allowlisted, and deduplicated for five seconds per event.
    _require_cooldown(
        db,
        user_id=current_user.id,
        action=f"event:{payload.event_name}",
        cooldown_seconds=5,
    )
    _record_product_event_once(db, current_user.id, payload.event_name)
    db.commit()
    return Response(status_code=204)
