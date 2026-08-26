from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import main
from app.models import (
    Base,
    NotificationDelivery,
    RepositoryPollState,
    Subscription,
    User,
)
from app.services.github import GitHubRateLimitError
from app.services.discord import DiscordDeliveryError
from app.routers.subscriptions import notification_history


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _seed_user_and_subscription(factory, *, checked_at: datetime):
    db = factory()
    user = User(
        discord_id="discord-1",
        username="tester",
        github_token="github-token",
    )
    db.add(user)
    db.flush()
    subscription = Subscription(
        user_id=user.id,
        repo_full_name="acme/project",
        label="good.*issue",
        last_checked_at=checked_at,
    )
    db.add(subscription)
    db.commit()
    values = user.id, subscription.id
    db.close()
    return values


def test_enqueue_is_persistently_idempotent(session_factory) -> None:
    user_id, _ = _seed_user_and_subscription(
        session_factory, checked_at=datetime(2026, 8, 23, 10, 0, 0)
    )
    issue = {
        "id": 1001,
        "number": 7,
        "title": "Starter task",
        "html_url": "https://github.com/acme/project/issues/7",
        "user": {"login": "octocat"},
        "labels": [{"name": "good first issue"}],
    }
    db = session_factory()

    first = main._enqueue_issue_delivery(
        db,
        user_id=user_id,
        repo="acme/project",
        issue=issue,
        matched_label="good first issue",
    )
    second = main._enqueue_issue_delivery(
        db,
        user_id=user_id,
        repo="acme/project",
        issue=issue,
        matched_label="good first issue",
    )

    assert first.id == second.id
    assert db.query(NotificationDelivery).count() == 1
    db.close()


def test_distinct_actionable_events_for_one_issue_are_each_delivered_once(
    session_factory,
) -> None:
    user_id, _ = _seed_user_and_subscription(
        session_factory,
        checked_at=datetime(2026, 8, 23, 10, 0, 0),
    )
    issue = {
        "id": 1009,
        "number": 11,
        "title": "Help requested again",
        "labels": [{"name": "good first issue"}],
    }
    db = session_factory()

    for event_id in ("EV_1", "EV_1", "EV_2"):
        main._enqueue_issue_delivery(
            db,
            user_id=user_id,
            repo="acme/project",
            issue=issue,
            matched_label="good first issue",
            trigger_key=f"event:{event_id}",
            trigger_reason="label_added",
        )

    deliveries = db.query(NotificationDelivery).order_by(NotificationDelivery.id).all()
    assert len(deliveries) == 2
    assert [delivery.trigger_event_id for delivery in deliveries] == ["EV_1", "EV_2"]
    assert all(delivery.source_issue_id == "1009" for delivery in deliveries)
    db.close()


def test_notification_history_exposes_source_issue_and_trigger_separately(
    session_factory,
) -> None:
    user_id, _ = _seed_user_and_subscription(
        session_factory,
        checked_at=datetime(2026, 8, 23, 10, 0, 0),
    )
    db = session_factory()
    user = db.get(User, user_id)
    main._enqueue_issue_delivery(
        db,
        user_id=user_id,
        repo="acme/project",
        issue={"id": 1010, "number": 12, "labels": [{"name": "good first issue"}]},
        matched_label="good first issue",
        trigger_key="event:EV_3",
        trigger_reason="label_added",
    )

    history = notification_history(limit=50, current_user=user, db=db)

    assert history[0]["issue_id"] == "1010"
    assert history[0]["trigger_type"] == "label_added"
    assert history[0]["trigger_event_id"] == "EV_3"
    db.close()


def test_assigned_new_issue_alerts_only_for_a_label_added_after_creation() -> None:
    subscription = Subscription(
        repo_full_name="acme/project",
        label="good.*issue",
        last_checked_at=datetime(2026, 8, 23, 10, 0, 0),
    )
    issue = {
        "id": 1002,
        "number": 8,
        "created_at": "2026-08-23T10:01:00Z",
        "labels": [{"name": "good first issue"}],
        "assignees": [],
    }

    trigger = main._select_issue_trigger(issue, [subscription], events=[])
    assert trigger == {
        "key": "created",
        "reason": "created",
        "matched_label": "good first issue",
    }

    issue["assignees"] = [{"login": "maintainer"}]
    events = [
        {
            "id": 1003,
            "event": "labeled",
            "created_at": "2026-08-23T10:01:30Z",
            "label": {"name": "good first issue"},
        }
    ]
    assert main._select_issue_trigger(issue, [subscription], events) == {
        "key": "event:1003",
        "reason": "label_added",
        "matched_label": "good first issue",
    }

    events[0]["created_at"] = issue["created_at"]
    assert main._select_issue_trigger(issue, [subscription], events) is None


def test_transient_actionable_state_must_still_be_current() -> None:
    subscription = Subscription(
        repo_full_name="acme/project",
        label="good.*issue",
        last_checked_at=datetime(2026, 8, 23, 10, 0, 0),
    )
    issue = {
        "id": 1004,
        "number": 9,
        "created_at": "2024-01-01T00:00:00Z",
        "labels": [],
        "assignees": [],
    }
    labeled = {
        "id": 1005,
        "event": "labeled",
        "created_at": "2026-08-23T10:01:00Z",
        "label": {"name": "good first issue"},
    }
    assert main._select_issue_trigger(issue, [subscription], [labeled]) is None

    issue["labels"] = [{"name": "good first issue"}]
    issue["assignees"] = [{"login": "maintainer"}]
    unassigned = {
        "id": 1006,
        "event": "unassigned",
        "created_at": "2026-08-23T10:01:00Z",
    }
    assert main._select_issue_trigger(issue, [subscription], [unassigned]) is None


def test_reopened_unassigned_matching_issue_is_actionable() -> None:
    subscription = Subscription(
        repo_full_name="acme/project",
        label="good.*issue",
        last_checked_at=datetime(2026, 8, 23, 10, 0, 0),
    )
    issue = {
        "id": 1007,
        "number": 10,
        "created_at": "2024-01-01T00:00:00Z",
        "labels": [{"name": "good first issue"}],
        "assignees": [],
    }
    reopened = {
        "id": 1008,
        "event": "reopened",
        "created_at": "2026-08-23T10:01:00Z",
    }
    assert main._select_issue_trigger(issue, [subscription], [reopened]) == {
        "key": "event:1008",
        "reason": "reopened",
        "matched_label": "good first issue",
    }


def test_each_watch_uses_its_own_checkpoint() -> None:
    earlier_watch = Subscription(
        repo_full_name="acme/project",
        label="good.*issue",
        last_checked_at=datetime(2026, 8, 23, 10, 0, 0),
    )
    later_watch = Subscription(
        repo_full_name="acme/project",
        label="good.*issue",
        last_checked_at=datetime(2026, 8, 23, 10, 2, 0),
    )
    issue = {
        "id": 1011,
        "number": 13,
        "created_at": "2024-01-01T00:00:00Z",
        "labels": [{"name": "good first issue"}],
        "assignees": [{"login": "maintainer"}],
    }
    event = {
        "id": 1012,
        "event": "labeled",
        "created_at": "2026-08-23T10:01:00Z",
        "label": {"name": "good first issue"},
    }

    assert main._select_issue_trigger(issue, [later_watch], [event]) is None
    assert main._select_issue_trigger(issue, [earlier_watch, later_watch], [event]) == {
        "key": "event:1012",
        "reason": "label_added",
        "matched_label": "good first issue",
    }


@pytest.mark.asyncio
async def test_failed_fetch_does_not_advance_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    original_checkpoint = datetime(2026, 8, 23, 10, 0, 0)
    _, subscription_id = _seed_user_and_subscription(
        session_factory, checked_at=original_checkpoint
    )

    async def fail_fetch(*args, **kwargs):
        raise GitHubRateLimitError("rate limited")

    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(main, "fetch_new_issues", fail_fetch)
    await main.poll_all_users()

    db = session_factory()
    subscription = db.get(Subscription, subscription_id)
    state = db.query(RepositoryPollState).one()
    assert subscription.last_checked_at == original_checkpoint
    assert state.error_code == "github_rate_limit"
    assert state.last_success_at is None
    db.close()


@pytest.mark.asyncio
async def test_label_added_after_creation_is_delivered_once(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    checkpoint = datetime(2026, 8, 23, 10, 0, 0)
    _seed_user_and_subscription(session_factory, checked_at=checkpoint)
    issue = {
        "id": 2002,
        "number": 12,
        "title": "Now beginner friendly",
        "html_url": "https://github.com/acme/project/issues/12",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2026-08-23T10:01:00Z",
        "user": {"login": "octocat"},
        "labels": [{"name": "good first issue"}],
        "assignees": [{"login": "maintainer"}],
    }
    events = [
        {
            "id": 8008,
            "event": "labeled",
            "created_at": "2026-08-23T10:01:00Z",
            "label": {"name": "good first issue"},
        }
    ]
    sent: list[str] = []

    async def fetch(*args, **kwargs):
        return [issue]

    async def fetch_events(*args, **kwargs):
        return {12: events}, {}

    async def send(discord_id: str, message: str):
        sent.append(message)

    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(main, "fetch_new_issues", fetch)
    monkeypatch.setattr(main, "fetch_issue_events", fetch_events)
    monkeypatch.setattr(main, "send_dm", send)

    await main.poll_all_users()
    await main.deliver_notifications()
    await main.poll_all_users()  # inclusive GitHub windows may return it again
    await main.deliver_notifications()

    db = session_factory()
    delivery = db.query(NotificationDelivery).one()
    assert delivery.status == "sent"
    assert delivery.attempt_count == 1
    assert delivery.issue_id == "2002:label_added:8008"
    assert delivery.source_issue_id == "2002"
    assert delivery.trigger_type == "label_added"
    assert delivery.trigger_event_id == "8008"
    assert len(sent) == 1
    assert "Now beginner friendly" in sent[0]
    assert "was just added" in sent[0]
    assert "Assigned to **maintainer**" in sent[0]
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("assigned", [False, True])
async def test_comment_only_update_does_not_enqueue_notification(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
    assigned: bool,
) -> None:
    checkpoint = datetime(2026, 8, 23, 10, 0, 0)
    _, subscription_id = _seed_user_and_subscription(
        session_factory,
        checked_at=checkpoint,
    )
    issue = {
        "id": 2112,
        "number": 13,
        "title": "Old issue with a new comment",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2026-08-23T10:01:00Z",
        "user": {"login": "octocat"},
        "labels": [{"name": "good first issue"}],
        "assignees": [{"login": "maintainer"}] if assigned else [],
    }

    async def fetch(*args, **kwargs):
        return [issue]

    async def fetch_events(*args, **kwargs):
        return {
            13: [
                {
                    "id": 99,
                    "event": "commented",
                    "created_at": "2026-08-23T10:01:00Z",
                }
            ]
        }, {}

    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(main, "fetch_new_issues", fetch)
    monkeypatch.setattr(main, "fetch_issue_events", fetch_events)

    await main.poll_all_users()

    db = session_factory()
    assert db.query(NotificationDelivery).count() == 0
    assert db.get(Subscription, subscription_id).last_checked_at > checkpoint
    state = db.query(RepositoryPollState).one()
    assert state.last_examined_count == 1
    assert state.last_ignored_update_count == 1
    db.close()


@pytest.mark.asyncio
async def test_existing_matching_issue_is_delivered_when_it_becomes_unassigned(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    checkpoint = datetime(2026, 8, 23, 10, 0, 0)
    _seed_user_and_subscription(session_factory, checked_at=checkpoint)
    issue = {
        "id": 2113,
        "number": 14,
        "title": "Available again",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2026-08-23T10:01:00Z",
        "user": {"login": "octocat"},
        "labels": [{"name": "good first issue"}],
        "assignees": [],
    }

    async def fetch(*args, **kwargs):
        return [issue]

    async def fetch_events(*args, **kwargs):
        return {
            14: [
                {
                    "id": 8009,
                    "event": "unassigned",
                    "created_at": "2026-08-23T10:01:00Z",
                }
            ]
        }, {}

    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(main, "fetch_new_issues", fetch)
    monkeypatch.setattr(main, "fetch_issue_events", fetch_events)

    await main.poll_all_users()

    db = session_factory()
    delivery = db.query(NotificationDelivery).one()
    assert delivery.issue_id == "2113:unassigned:8009"
    assert "became unassigned" in delivery.message
    db.close()


@pytest.mark.asyncio
async def test_one_event_failure_does_not_block_other_issues_or_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    checkpoint = datetime(2026, 8, 23, 10, 0, 0)
    _, subscription_id = _seed_user_and_subscription(
        session_factory,
        checked_at=checkpoint,
    )
    issues = [
        {
            "id": 2201,
            "number": 21,
            "title": "Needs help",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2026-08-23T10:01:00Z",
            "labels": [{"name": "good first issue"}],
            "assignees": [{"login": "maintainer"}],
        },
        {
            "id": 2202,
            "number": 22,
            "title": "Unavailable timeline",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2026-08-23T10:01:00Z",
            "labels": [{"name": "good first issue"}],
            "assignees": [],
        },
    ]

    async def fetch(*args, **kwargs):
        return issues

    async def fetch_events(*args, **kwargs):
        return {
            21: [
                {
                    "id": 9101,
                    "event": "labeled",
                    "created_at": "2026-08-23T10:01:00Z",
                    "label": {"name": "good first issue"},
                }
            ]
        }, {22: "Timeline is gone"}

    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(main, "fetch_new_issues", fetch)
    monkeypatch.setattr(main, "fetch_issue_events", fetch_events)

    await main.poll_all_users()

    db = session_factory()
    assert db.query(NotificationDelivery).count() == 1
    subscription = db.get(Subscription, subscription_id)
    state = db.query(RepositoryPollState).one()
    assert subscription.last_checked_at > checkpoint
    assert state.last_success_at is not None
    assert state.last_examined_count == 2
    assert state.last_actionable_count == 1
    assert state.last_event_failure_count == 1
    db.close()


@pytest.mark.asyncio
async def test_event_rate_limit_still_blocks_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    checkpoint = datetime(2026, 8, 23, 10, 0, 0)
    _, subscription_id = _seed_user_and_subscription(
        session_factory,
        checked_at=checkpoint,
    )
    issue = {
        "id": 2203,
        "number": 23,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2026-08-23T10:01:00Z",
        "labels": [{"name": "good first issue"}],
        "assignees": [],
    }

    async def fetch(*args, **kwargs):
        return [issue]

    async def fetch_events(*args, **kwargs):
        raise GitHubRateLimitError("rate limited")

    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(main, "fetch_new_issues", fetch)
    monkeypatch.setattr(main, "fetch_issue_events", fetch_events)

    await main.poll_all_users()

    db = session_factory()
    assert db.get(Subscription, subscription_id).last_checked_at == checkpoint
    assert db.query(RepositoryPollState).one().error_code == "github_rate_limit"
    db.close()


@pytest.mark.asyncio
async def test_failed_dm_remains_retryable(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    user_id, _ = _seed_user_and_subscription(
        session_factory, checked_at=datetime(2026, 8, 23, 10, 0, 0)
    )
    db = session_factory()
    delivery = NotificationDelivery(
        user_id=user_id,
        delivery_type="issue",
        repo_full_name="acme/project",
        issue_id="3003",
        issue_number=3,
        matched_label="bug",
        message="test message",
        status="pending",
        next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(seconds=1),
    )
    db.add(delivery)
    db.commit()

    async def fail(*args, **kwargs):
        raise RuntimeError("Discord unavailable")

    monkeypatch.setattr(main, "send_dm", fail)
    await main._deliver_due_notifications(db)
    db.refresh(delivery)
    assert delivery.status == "failed"
    assert delivery.attempt_count == 1
    assert delivery.next_attempt_at is not None

    delivery.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        seconds=1
    )
    db.commit()

    async def succeed(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "send_dm", succeed)
    await main._deliver_due_notifications(db)
    db.refresh(delivery)
    assert delivery.status == "sent"
    assert delivery.attempt_count == 2
    db.close()


@pytest.mark.asyncio
async def test_corrupt_token_is_isolated_from_other_users(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    db = session_factory()
    broken = User(
        discord_id="discord-broken",
        username="broken",
        github_token="will-be-corrupted",
    )
    healthy = User(
        discord_id="discord-healthy",
        username="healthy",
        github_token="healthy-token",
    )
    db.add_all((broken, healthy))
    db.flush()
    db.add_all(
        (
            Subscription(
                user_id=broken.id,
                repo_full_name="acme/broken",
                label="bug",
            ),
            Subscription(
                user_id=healthy.id,
                repo_full_name="acme/healthy",
                label="bug",
            ),
        )
    )
    db.commit()
    broken_id = broken.id
    db.execute(
        text("UPDATE users SET github_token = :token WHERE id = :user_id"),
        {"token": "fernet:v1:not-a-valid-token", "user_id": broken_id},
    )
    db.commit()
    db.close()

    fetched: list[str] = []

    async def fetch(repo: str, token: str, since: datetime | None, **kwargs):
        fetched.append(repo)
        return []

    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(main, "fetch_new_issues", fetch)
    await main.poll_all_users()

    db = session_factory()
    broken_state = db.query(RepositoryPollState).filter(
        RepositoryPollState.user_id == broken_id
    ).one()
    assert broken_state.error_code == "token_decryption_error"
    assert fetched == ["acme/healthy"]
    db.close()


@pytest.mark.asyncio
async def test_delivery_does_not_decrypt_unrelated_github_token(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    user_id, _ = _seed_user_and_subscription(
        session_factory, checked_at=datetime(2026, 8, 23, 10, 0, 0)
    )
    db = session_factory()
    db.add(
        NotificationDelivery(
            user_id=user_id,
            delivery_type="issue",
            repo_full_name="acme/project",
            issue_id="corrupt-token-delivery",
            message="still deliver this",
            status="pending",
            next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    db.commit()
    db.execute(
        text("UPDATE users SET github_token = :token WHERE id = :user_id"),
        {"token": "fernet:v1:not-a-valid-token", "user_id": user_id},
    )
    db.commit()

    sent: list[str] = []

    async def send(discord_id: str, message: str):
        sent.append(message)

    monkeypatch.setattr(main, "send_dm", send)
    await main._deliver_due_notifications(db)

    delivery = db.query(NotificationDelivery).filter(
        NotificationDelivery.issue_id == "corrupt-token-delivery"
    ).one()
    assert delivery.status == "sent"
    assert sent == ["still deliver this"]
    db.close()


@pytest.mark.asyncio
async def test_permanent_discord_rejection_is_not_retried_forever(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
) -> None:
    user_id, _ = _seed_user_and_subscription(
        session_factory, checked_at=datetime(2026, 8, 23, 10, 0, 0)
    )
    db = session_factory()
    delivery = NotificationDelivery(
        user_id=user_id,
        delivery_type="issue",
        repo_full_name="acme/project",
        issue_id="permanent-discord-error",
        message="cannot deliver",
        status="pending",
        next_attempt_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(delivery)
    db.commit()

    async def reject(*args, **kwargs):
        raise DiscordDeliveryError("Discord rejected", retryable=False, status_code=403)

    monkeypatch.setattr(main, "send_dm", reject)
    await main._deliver_due_notifications(db)
    db.refresh(delivery)

    assert delivery.status == "dead"
    assert delivery.next_attempt_at is None
    assert delivery.attempt_count == 1
    db.close()
