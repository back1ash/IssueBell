from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


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
    github_token: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription", back_populates="user", cascade="all, delete-orphan"
    )


class Subscription(Base):
    """User's subscription to a GitHub repo + label combination."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "repo_full_name", "label", name="uq_user_repo_label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    # e.g. "octocat/Hello-World"
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # regex pattern, e.g. "good.first.issue" or "help.*"
    label: Mapped[str] = mapped_column(String, nullable=False)
    # timestamp of last successful poll for this subscription
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="subscriptions")
