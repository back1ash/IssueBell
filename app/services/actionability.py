"""Shared GitHub issue actionability rules.

The polling worker and the pre-subscription preview both use this module so a
preview cannot promise an alert that the worker would later suppress (or vice
versa).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from app.services.github import match_label


class WatchRule(Protocol):
    label: str
    last_checked_at: datetime | None
    created_at: datetime | None


def parse_github_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def subscription_cursor(subscription: WatchRule) -> datetime | None:
    return subscription.last_checked_at or subscription.created_at


def issue_labels(issue: dict[str, Any]) -> list[str]:
    raw_labels = issue.get("labels")
    if not isinstance(raw_labels, list):
        return []
    return [
        str(label.get("name", ""))
        for label in raw_labels
        if isinstance(label, dict)
    ]


def issue_is_assigned(issue: dict[str, Any]) -> bool:
    return bool(issue.get("assignee") or issue.get("assignees"))


def select_issue_trigger(
    issue: dict[str, Any],
    subscriptions: list[WatchRule],
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select the latest actionable transition for one issue.

    New issues are actionable only while unassigned. Existing issues become
    actionable when a watched label is added or their assignee is removed.
    Comment, title, and body updates do not produce any of these triggers.
    """

    issue_created_at = parse_github_timestamp(issue.get("created_at"))
    current_labels = issue_labels(issue)
    is_assigned = issue_is_assigned(issue)
    candidates: list[tuple[datetime, int, dict[str, Any]]] = []

    for subscription in subscriptions:
        cursor = subscription_cursor(subscription)
        matched_current_label = match_label(subscription.label, current_labels)
        if (
            not is_assigned
            and matched_current_label is not None
            and issue_created_at is not None
            and (cursor is None or issue_created_at >= cursor.replace(microsecond=0))
        ):
            candidates.append(
                (
                    issue_created_at,
                    0,
                    {
                        "key": "created",
                        "reason": "created",
                        "matched_label": matched_current_label,
                    },
                )
            )

        for event in events:
            event_at = parse_github_timestamp(event.get("created_at"))
            if event_at is None or (
                cursor is not None and event_at < cursor.replace(microsecond=0)
            ):
                continue

            event_type = event.get("event")
            event_id = str(
                event.get("id")
                or event.get("node_id")
                or f"{event_type}:{event.get('created_at')}"
            )
            if event_type == "labeled":
                # GitHub records labels supplied during issue creation at the
                # same second as the issue. An assigned new issue should not
                # alert for those initial labels, but a later help label should.
                if (
                    is_assigned
                    and issue_created_at is not None
                    and event_at <= issue_created_at
                ):
                    continue
                raw_label = event.get("label")
                event_label = (
                    str(raw_label.get("name", ""))
                    if isinstance(raw_label, dict)
                    else ""
                )
                if not any(
                    event_label.casefold() == current_label.casefold()
                    for current_label in current_labels
                ):
                    continue
                matched_event_label = match_label(subscription.label, [event_label])
                if matched_event_label is not None:
                    candidates.append(
                        (
                            event_at,
                            2,
                            {
                                "key": f"event:{event_id}",
                                "reason": "label_added",
                                "matched_label": matched_event_label,
                            },
                        )
                    )
            elif (
                event_type == "unassigned"
                and not is_assigned
                and matched_current_label is not None
            ):
                candidates.append(
                    (
                        event_at,
                        1,
                        {
                            "key": f"event:{event_id}",
                            "reason": "unassigned",
                            "matched_label": matched_current_label,
                        },
                    )
                )
            elif (
                event_type == "reopened"
                and not is_assigned
                and matched_current_label is not None
            ):
                candidates.append(
                    (
                        event_at,
                        1,
                        {
                            "key": f"event:{event_id}",
                            "reason": "reopened",
                            "matched_label": matched_current_label,
                        },
                    )
                )

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]
