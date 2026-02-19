import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# ─── User ──────────────────────────────────────────────────────────────────────

class UserBase(BaseModel):
    discord_id: str
    username: str
    avatar: str | None = None


class UserRead(UserBase):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Subscription ──────────────────────────────────────────────────────────────

class SubscriptionCreate(BaseModel):
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
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc
        return v


class SubscriptionRead(SubscriptionCreate):
    id: int
    user_id: int
    created_at: datetime

    model_config = {"from_attributes": True}
