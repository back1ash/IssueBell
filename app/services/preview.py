"""Bounded, read-only previews of subscription alert behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.services.actionability import (
    issue_labels,
    parse_github_timestamp,
    select_issue_trigger,
)
from app.services.github import (
    GitHubLabelNotFoundError,
    fetch_issue_events,
    fetch_recent_issues_limited,
    fetch_repository_labels,
    match_label,
)


PREVIEW_WINDOW_DAYS = 30
PREVIEW_ISSUE_LIMIT = 300
PREVIEW_EVENT_ISSUE_LIMIT = 100
PREVIEW_SAMPLE_LIMIT = 10
NOISY_PREVIEW_THRESHOLD = 20

_TRIGGER_REASONS = {
    "created": "New unassigned issue matching your watch",
    "label_added": "Watched label was added recently",
    "unassigned": "Issue became unassigned",
    "reopened": "Matching issue reopened while unassigned",
}


@dataclass(frozen=True, slots=True)
class PreviewWatch:
    label: str
    last_checked_at: datetime | None
    created_at: datetime | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _issue_matches_watches(issue: dict[str, Any], watches: list[PreviewWatch]) -> bool:
    labels = issue_labels(issue)
    return any(match_label(watch.label, labels) is not None for watch in watches)


def _triggered_at(
    issue: dict[str, Any],
    events: list[dict[str, Any]],
    trigger: dict[str, Any],
) -> datetime | None:
    if trigger.get("reason") == "created":
        return parse_github_timestamp(issue.get("created_at"))
    event_id = str(trigger.get("key", "")).removeprefix("event:")
    for event in events:
        candidate_id = str(
            event.get("id")
            or event.get("node_id")
            or f"{event.get('event')}:{event.get('created_at')}"
        )
        if candidate_id == event_id:
            return parse_github_timestamp(event.get("created_at"))
    return parse_github_timestamp(issue.get("updated_at"))


def _assignee_names(issue: dict[str, Any]) -> list[str]:
    raw_assignees = issue.get("assignees")
    names = (
        [
            str(assignee.get("login", ""))
            for assignee in raw_assignees
            if isinstance(assignee, dict) and assignee.get("login")
        ]
        if isinstance(raw_assignees, list)
        else []
    )
    if not names and isinstance(issue.get("assignee"), dict):
        login = issue["assignee"].get("login")
        if login:
            names.append(str(login))
    return names[:10]


def _preview_item(
    repo: str,
    issue: dict[str, Any],
    events: list[dict[str, Any]],
    trigger: dict[str, Any],
) -> dict[str, Any] | None:
    number = issue.get("number")
    if not isinstance(number, int):
        return None
    trigger_type = str(trigger.get("reason") or "created")
    assignees = _assignee_names(issue)
    return {
        "issue_number": number,
        "title": str(issue.get("title") or "(no title)")[:300],
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "matched_label": str(trigger.get("matched_label") or "")[:200],
        "trigger_type": trigger_type,
        "trigger_reason": _TRIGGER_REASONS.get(
            trigger_type, "Issue became actionable"
        ),
        "triggered_at": _triggered_at(issue, events, trigger),
        "created_at": parse_github_timestamp(issue.get("created_at")),
        "updated_at": parse_github_timestamp(issue.get("updated_at")),
        "assigned_to": assignees,
    }


async def build_subscription_preview(
    repo: str,
    labels: list[str],
    token: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Estimate recent alerts using the exact production trigger selector."""

    generated_at = now or _utcnow()
    if generated_at.tzinfo is not None:
        generated_at = generated_at.astimezone(timezone.utc).replace(tzinfo=None)
    window_started_at = generated_at - timedelta(days=PREVIEW_WINDOW_DAYS)
    watches = [
        PreviewWatch(
            label=label,
            last_checked_at=window_started_at,
            created_at=window_started_at,
        )
        for label in labels
    ]

    async with httpx.AsyncClient(timeout=settings.outbound_http_timeout) as client:
        repository, repository_labels = await fetch_repository_labels(
            repo,
            token,
            client=client,
        )
        available_labels = [str(label.get("name", "")) for label in repository_labels]
        for label in labels:
            if match_label(label, available_labels) is None:
                raise GitHubLabelNotFoundError(label, available_labels)

        issues, issue_list_truncated = await fetch_recent_issues_limited(
            repo,
            token,
            window_started_at,
            limit=PREVIEW_ISSUE_LIMIT,
            client=client,
        )

        actionable_items: list[dict[str, Any]] = []
        event_candidates: list[dict[str, Any]] = []
        matching_issue_count = 0
        for issue in issues:
            if not _issue_matches_watches(issue, watches):
                continue
            matching_issue_count += 1
            trigger = select_issue_trigger(issue, watches, events=[])
            if trigger is not None:
                item = _preview_item(repo, issue, [], trigger)
                if item is not None:
                    actionable_items.append(item)
                continue
            if isinstance(issue.get("number"), int):
                event_candidates.append(issue)

        event_candidates.sort(
            key=lambda issue: issue.get("updated_at") or issue.get("created_at") or "",
            reverse=True,
        )
        event_candidates_truncated = len(event_candidates) > PREVIEW_EVENT_ISSUE_LIMIT
        evaluated_event_candidates = event_candidates[:PREVIEW_EVENT_ISSUE_LIMIT]
        events_by_issue, event_failures = await fetch_issue_events(
            repo,
            [int(issue["number"]) for issue in evaluated_event_candidates],
            token,
            window_started_at,
            client=client,
        )
        for issue in evaluated_event_candidates:
            issue_number = int(issue["number"])
            if issue_number in event_failures:
                continue
            events = events_by_issue.get(issue_number, [])
            trigger = select_issue_trigger(issue, watches, events)
            if trigger is None:
                continue
            item = _preview_item(repo, issue, events, trigger)
            if item is not None:
                actionable_items.append(item)

    actionable_items.sort(
        key=lambda item: item.get("triggered_at") or datetime.min,
        reverse=True,
    )
    is_partial = bool(
        issue_list_truncated or event_candidates_truncated or event_failures
    )
    warnings: list[str] = []
    if issue_list_truncated:
        warnings.append(
            f"This busy repository had more than {PREVIEW_ISSUE_LIMIT} recent updates; "
            "the estimate uses the newest ones."
        )
    if event_candidates_truncated:
        warnings.append(
            f"Only the {PREVIEW_EVENT_ISSUE_LIMIT} most recently updated matching "
            "issues were checked for label and assignment events."
        )
    if event_failures:
        warnings.append(
            f"GitHub could not provide {len(event_failures)} issue "
            f"timeline{'s' if len(event_failures) != 1 else ''}; those issues were omitted."
        )
    if len(actionable_items) >= NOISY_PREVIEW_THRESHOLD:
        warnings.append(
            "This watch may be noisy. Consider choosing a narrower label pattern."
        )

    return {
        "repo_full_name": str(repository.get("full_name") or repo),
        "window_days": PREVIEW_WINDOW_DAYS,
        "window_started_at": window_started_at,
        "generated_at": generated_at,
        "examined_issue_count": len(issues),
        "matching_issue_count": matching_issue_count,
        "estimated_notification_count": len(actionable_items),
        "is_partial": is_partial,
        "warnings": warnings,
        "issues": actionable_items[:PREVIEW_SAMPLE_LIMIT],
    }
