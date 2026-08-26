from sqlalchemy import create_engine, inspect, text

from app.schema_migrations import migrate_actionable_notifications


def test_actionable_notification_migration_is_idempotent_and_cancels_legacy_queue():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE notification_deliveries (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    delivery_type VARCHAR NOT NULL,
                    repo_full_name VARCHAR NOT NULL,
                    issue_id VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    next_attempt_at DATETIME,
                    lease_until DATETIME,
                    last_error TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE repository_poll_states (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    repo_full_name VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO notification_deliveries
                    (id, user_id, delivery_type, repo_full_name, issue_id, status)
                VALUES
                    (1, 7, 'issue', 'acme/project', '12345', 'pending'),
                    (2, 7, 'issue', 'acme/project', '12345:event:EV_2', 'sent'),
                    (3, 7, 'test', '__test__', 'test:abc', 'sent')
                """
            )
        )

    assert migrate_actionable_notifications(engine) == 1
    assert migrate_actionable_notifications(engine) == 0

    delivery_columns = {
        column["name"] for column in inspect(engine).get_columns("notification_deliveries")
    }
    assert {"source_issue_id", "trigger_type", "trigger_event_id"} <= delivery_columns
    poll_columns = {
        column["name"] for column in inspect(engine).get_columns("repository_poll_states")
    }
    assert {
        "last_examined_count",
        "last_actionable_count",
        "last_ignored_update_count",
        "last_event_failure_count",
    } <= poll_columns

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, status, source_issue_id, trigger_type, trigger_event_id "
                "FROM notification_deliveries ORDER BY id"
            )
        ).mappings().all()
    assert rows[0]["status"] == "cancelled"
    assert rows[0]["source_issue_id"] == "12345"
    assert rows[0]["trigger_type"] == "legacy"
    assert rows[1]["source_issue_id"] == "12345"
    assert rows[1]["trigger_type"] == "event"
    assert rows[1]["trigger_event_id"] == "EV_2"
    assert rows[2]["trigger_type"] == "test"
