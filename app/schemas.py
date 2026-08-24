from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.services.github import compile_label_pattern


class UTCResponseModel(BaseModel):
    """Serialize database-naive UTC values with an explicit UTC offset."""

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_utc_datetimes(self, value):
        if isinstance(value, datetime):
            aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value


# ─── User ──────────────────────────────────────────────────────────────────────

class UserBase(UTCResponseModel):
    discord_id: str
    username: str
    avatar: str | None = None


class UserRead(UserBase):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Subscription ──────────────────────────────────────────────────────────────

class SubscriptionCreate(UTCResponseModel):
    repo_full_name: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
        examples=["octocat/Hello-World"],
    )
    label: str = Field(..., min_length=1, max_length=200, examples=["good-first-issue"])

    @field_validator("repo_full_name")
    @classmethod
    def normalize_repo(cls, v: str) -> str:
        return v.lower()

    @field_validator("label")
    @classmethod
    def label_must_be_valid_regex(cls, v: str) -> str:
        try:
            compile_label_pattern(v)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc
        return v


class SubscriptionRead(SubscriptionCreate):
    id: int
    user_id: int
    last_checked_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RepositoryLabelRead(UTCResponseModel):
    name: str
    color: str = ""
    description: str | None = None


class RepositoryLabelsRead(BaseModel):
    repo_full_name: str
    html_url: str
    description: str | None = None
    private: bool = False
    labels: list[RepositoryLabelRead]


class PollStatusRead(UTCResponseModel):
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    rate_limit_reset_at: datetime | None = None


class DeliverySummaryRead(UTCResponseModel):
    sent_count: int = 0
    pending_count: int = 0
    failed_count: int = 0
    dead_count: int = 0
    last_sent_at: datetime | None = None


class SubscriptionStatusItemRead(SubscriptionRead):
    poll: PollStatusRead | None = None
    delivery: DeliverySummaryRead


class SubscriptionStatusResponse(BaseModel):
    github_connected: bool
    subscriptions: list[SubscriptionStatusItemRead]


class NotificationHistoryRead(UTCResponseModel):
    id: int
    delivery_type: str
    repo_full_name: str
    issue_id: str
    issue_number: int | None = None
    matched_label: str | None = None
    status: str
    attempt_count: int
    last_error: str | None = None
    last_attempt_at: datetime | None = None
    sent_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TestDMRead(UTCResponseModel):
    status: str
    sent_at: datetime


class ProductEventCreate(UTCResponseModel):
    event_name: Literal[
        "dashboard_view",
        "subscription_form_viewed",
        "github_connect_started",
    ]
