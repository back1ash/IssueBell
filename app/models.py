from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.token_crypto import EncryptedToken


class User(Base):
    """A user authenticated via Discord OAuth2."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    discord_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False)
    avatar: Mapped[str | None] = mapped_column(String, nullable=True)
    # GitHub OAuth
    github_id: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)
    github_username: Mapped[str | None] = mapped_column(String, nullable=True)
    github_token: Mapped[str | None] = mapped_column(
        EncryptedToken(), nullable=True, deferred=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription", back_populates="user", cascade="all, delete-orphan"
    )
    notification_deliveries: Mapped[list["NotificationDelivery"]] = relationship(
        "NotificationDelivery", back_populates="user", cascade="all, delete-orphan"
    )
    repository_poll_states: Mapped[list["RepositoryPollState"]] = relationship(
        "RepositoryPollState", back_populates="user", cascade="all, delete-orphan"
    )
    action_throttles: Mapped[list["UserActionThrottle"]] = relationship(
        "UserActionThrottle", back_populates="user", cascade="all, delete-orphan"
    )
    product_events: Mapped[list["ProductEvent"]] = relationship(
        "ProductEvent", back_populates="user", cascade="all, delete-orphan"
    )
    auth_sessions: Mapped[list["AuthSession"]] = relationship(
        "AuthSession", back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    """Server-side authentication handle referenced by the signed cookie."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="auth_sessions")


class Subscription(Base):
    """User's subscription to a GitHub repo + label combination."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "repo_full_name", "label", name="uq_user_repo_label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # e.g. "octocat/Hello-World"
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # regex pattern, e.g. "good.first.issue" or "help.*"
    label: Mapped[str] = mapped_column(String, nullable=False)
    # timestamp of last successful poll for this subscription
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="subscriptions")


class NotificationDelivery(Base):
    """Durable notification outbox and delivery history.

    Each actionable GitHub issue transition is delivered at most once per user
    and repository. The source issue and trigger metadata remain separate for
    history/API consumers; ``issue_id`` is retained as a legacy-compatible
    delivery key. Failed attempts remain in this table so the scheduler can
    retry them without relying on a later GitHub poll.
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "repo_full_name",
            "issue_id",
            name="uq_notification_delivery_user_repo_issue",
        ),
        UniqueConstraint(
            "user_id",
            "repo_full_name",
            "source_issue_id",
            "trigger_type",
            "trigger_event_id",
            name="uq_notification_delivery_actionable_trigger",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delivery_type: Mapped[str] = mapped_column(String, nullable=False, default="issue")
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    issue_id: Mapped[str] = mapped_column(String, nullable=False)
    source_issue_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trigger_type: Mapped[str | None] = mapped_column(String, nullable=True)
    trigger_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched_label: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="notification_deliveries")


class RepositoryPollState(Base):
    """Last known GitHub polling health for one user's repository."""

    __tablename__ = "repository_poll_states"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "repo_full_name", name="uq_repository_poll_state_user_repo"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_limit_reset_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_examined_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_actionable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_ignored_update_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_event_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="repository_poll_states")


class UserActionThrottle(Base):
    """Persistent per-user cooldown used for external API actions."""

    __tablename__ = "user_action_throttles"
    __table_args__ = (
        UniqueConstraint("user_id", "action", name="uq_user_action_throttle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="action_throttles")


class ProductEvent(Base):
    """Allowlisted, payload-free product funnel event."""

    __tablename__ = "product_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="product_events")
