"""Small idempotent schema upgrades for deployments without Alembic."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def migrate_actionable_notifications(engine: Engine) -> int:
    """Add trigger metadata and cancel unsafe deliveries from the old policy."""

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "notification_deliveries" not in table_names:
        return 0

    delivery_columns = {
        column["name"] for column in inspector.get_columns("notification_deliveries")
    }
    poll_columns = (
        {
            column["name"]
            for column in inspector.get_columns("repository_poll_states")
        }
        if "repository_poll_states" in table_names
        else set()
    )

    with engine.begin() as connection:
        for column_name, column_type in (
            ("source_issue_id", "VARCHAR"),
            ("trigger_type", "VARCHAR"),
            ("trigger_event_id", "VARCHAR"),
        ):
            if column_name not in delivery_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE notification_deliveries "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

        for column_name in (
            "last_examined_count",
            "last_actionable_count",
            "last_ignored_update_count",
            "last_event_failure_count",
        ):
            if column_name not in poll_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE repository_poll_states "
                        f"ADD COLUMN {column_name} INTEGER NOT NULL DEFAULT 0"
                    )
                )

        rows = connection.execute(
            text(
                "SELECT id, delivery_type, issue_id, status, source_issue_id, "
                "trigger_type, trigger_event_id FROM notification_deliveries"
            )
        ).mappings()
        cancelled = 0
        for row in rows:
            source_issue_id = row["source_issue_id"]
            trigger_type = row["trigger_type"]
            trigger_event_id = row["trigger_event_id"]
            legacy_identity = False
            if not source_issue_id or not trigger_type or not trigger_event_id:
                raw_identity = str(row["issue_id"])
                if ":event:" in raw_identity:
                    source_issue_id, trigger_event_id = raw_identity.split(":event:", 1)
                    trigger_type = "event"
                elif raw_identity.endswith(":created"):
                    source_issue_id = raw_identity.removesuffix(":created")
                    trigger_type = "created"
                    trigger_event_id = "created"
                elif row["delivery_type"] == "test":
                    source_issue_id = raw_identity
                    trigger_type = "test"
                    trigger_event_id = raw_identity
                else:
                    source_issue_id = raw_identity
                    trigger_type = "legacy"
                    trigger_event_id = f"legacy:{row['id']}"
                    legacy_identity = True
                connection.execute(
                    text(
                        "UPDATE notification_deliveries SET "
                        "source_issue_id = :source_issue_id, "
                        "trigger_type = :trigger_type, "
                        "trigger_event_id = :trigger_event_id WHERE id = :id"
                    ),
                    {
                        "source_issue_id": source_issue_id,
                        "trigger_type": trigger_type,
                        "trigger_event_id": trigger_event_id,
                        "id": row["id"],
                    },
                )

            if (
                row["delivery_type"] == "issue"
                and (legacy_identity or trigger_type == "legacy")
                and row["status"] in {"pending", "failed", "sending"}
            ):
                connection.execute(
                    text(
                        "UPDATE notification_deliveries SET status = 'cancelled', "
                        "next_attempt_at = NULL, lease_until = NULL, "
                        "last_error = :reason WHERE id = :id"
                    ),
                    {
                        "reason": (
                            "Cancelled during actionable-event migration; "
                            "the legacy notification was not revalidated"
                        ),
                        "id": row["id"],
                    },
                )
                cancelled += 1

        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_notification_delivery_actionable_trigger "
                "ON notification_deliveries "
                "(user_id, repo_full_name, source_issue_id, "
                "trigger_type, trigger_event_id)"
            )
        )

    if cancelled:
        logger.warning(
            "Cancelled %s queued legacy notification(s) during schema migration",
            cancelled,
        )
    return cancelled
