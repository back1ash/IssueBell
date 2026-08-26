from datetime import datetime

import pytest

from app.schemas import SubscriptionPreviewCreate
from app.services import preview
from app.services.github import GitHubLabelNotFoundError


class _DummyClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_preview_uses_production_actionability_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issues = [
        {
            "id": 1,
            "number": 1,
            "title": "New task",
            "created_at": "2026-08-20T10:00:00Z",
            "updated_at": "2026-08-20T10:00:00Z",
            "labels": [{"name": "help needed"}],
            "assignees": [],
        },
        {
            "id": 2,
            "number": 2,
            "title": "Old issue asking for help",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2026-08-22T12:00:00Z",
            "labels": [{"name": "help needed"}],
            "assignees": [{"login": "maintainer"}],
        },
        {
            "id": 3,
            "number": 3,
            "title": "Comment-only update",
            "created_at": "2024-02-01T00:00:00Z",
            "updated_at": "2026-08-23T12:00:00Z",
            "labels": [{"name": "help needed"}],
            "assignees": [],
        },
        {
            "id": 4,
            "number": 4,
            "title": "Different label",
            "created_at": "2026-08-21T10:00:00Z",
            "updated_at": "2026-08-21T10:00:00Z",
            "labels": [{"name": "bug"}],
            "assignees": [],
        },
    ]

    async def fetch_labels(*args, **kwargs):
        return {"full_name": "acme/project"}, [{"name": "help needed"}]

    async def fetch_issues(*args, **kwargs):
        return issues, False

    async def fetch_events(*args, **kwargs):
        assert args[1] == [3, 2]
        return {
            2: [
                {
                    "id": "LE_2",
                    "event": "labeled",
                    "created_at": "2026-08-22T12:00:00Z",
                    "label": {"name": "help needed"},
                }
            ],
            3: [],
        }, {}

    monkeypatch.setattr(preview.httpx, "AsyncClient", lambda **kwargs: _DummyClient())
    monkeypatch.setattr(preview, "fetch_repository_labels", fetch_labels)
    monkeypatch.setattr(preview, "fetch_recent_issues_limited", fetch_issues)
    monkeypatch.setattr(preview, "fetch_issue_events", fetch_events)

    result = await preview.build_subscription_preview(
        "acme/project",
        ["help.*"],
        "token",
        now=datetime(2026, 8, 26, 0, 0, 0),
    )

    assert result["examined_issue_count"] == 4
    assert result["matching_issue_count"] == 3
    assert result["estimated_notification_count"] == 2
    assert result["is_partial"] is False
    assert [item["issue_number"] for item in result["issues"]] == [2, 1]
    assert result["issues"][0]["trigger_type"] == "label_added"
    assert result["issues"][0]["assigned_to"] == ["maintainer"]
    assert result["issues"][1]["trigger_type"] == "created"


@pytest.mark.asyncio
async def test_preview_marks_bounded_or_missing_timeline_results_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = {
        "id": 8,
        "number": 8,
        "title": "Timeline unavailable",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2026-08-24T12:00:00Z",
        "labels": [{"name": "help wanted"}],
        "assignees": [],
    }

    async def fetch_labels(*args, **kwargs):
        return {"full_name": "acme/project"}, [{"name": "help wanted"}]

    async def fetch_issues(*args, **kwargs):
        return [issue], True

    async def fetch_events(*args, **kwargs):
        return {}, {8: "Timeline unavailable"}

    monkeypatch.setattr(preview.httpx, "AsyncClient", lambda **kwargs: _DummyClient())
    monkeypatch.setattr(preview, "fetch_repository_labels", fetch_labels)
    monkeypatch.setattr(preview, "fetch_recent_issues_limited", fetch_issues)
    monkeypatch.setattr(preview, "fetch_issue_events", fetch_events)

    result = await preview.build_subscription_preview(
        "acme/project",
        ["help wanted"],
        "token",
        now=datetime(2026, 8, 26, 0, 0, 0),
    )

    assert result["is_partial"] is True
    assert result["estimated_notification_count"] == 0
    assert len(result["warnings"]) == 2
    assert "more than 300" in result["warnings"][0]
    assert "1 issue timeline" in result["warnings"][1]


@pytest.mark.asyncio
async def test_preview_requires_each_pattern_to_match_a_repository_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch_labels(*args, **kwargs):
        return {"full_name": "acme/project"}, [{"name": "bug"}]

    monkeypatch.setattr(preview.httpx, "AsyncClient", lambda **kwargs: _DummyClient())
    monkeypatch.setattr(preview, "fetch_repository_labels", fetch_labels)

    with pytest.raises(GitHubLabelNotFoundError):
        await preview.build_subscription_preview(
            "acme/project",
            ["help wanted"],
            "token",
            now=datetime(2026, 8, 26, 0, 0, 0),
        )


def test_preview_payload_normalizes_repo_and_deduplicates_patterns() -> None:
    payload = SubscriptionPreviewCreate(
        repo_full_name="Acme/Project",
        labels=[" help.* ", "help.*"],
    )

    assert payload.repo_full_name == "acme/project"
    assert payload.labels == ["help.*"]
