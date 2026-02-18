"""GitHub webhook receiver."""

import json
import logging
import re

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Subscription, User
from app.services.discord import build_issue_message, send_dm
from app.services.github import verify_signature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
):
    body = await request.body()

    # ── 1. Verify signature ────────────────────────────────────────────────────
    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # ── 2. Only handle 'issues' events with action 'opened' ───────────────────
    if x_github_event != "issues":
        return {"ok": True, "skipped": "not an issues event"}

    payload = await request.json() if not body else None
    # Re-parse from body to avoid double-read issues
    payload = json.loads(body)

    if payload.get("action") != "opened":
        return {"ok": True, "skipped": "action is not 'opened'"}

    issue = payload["issue"]
    repo_full_name: str = payload["repository"]["full_name"]
    issue_labels: set[str] = {lb["name"] for lb in issue.get("labels", [])}

    logger.info("New issue #%s in %s | labels: %s", issue["number"], repo_full_name, issue_labels)

    if not issue_labels:
        return {"ok": True, "skipped": "issue has no labels"}

    # ── 3. Find matching subscriptions (regex) ────────────────────────────────
    db: Session = SessionLocal()
    try:
        # Load all subscriptions for this repo, then match labels via regex
        subs = (
            db.query(Subscription)
            .filter(Subscription.repo_full_name == repo_full_name)
            .all()
        )

        def first_match(pattern: str, labels: set[str]) -> str | None:
            """Return the first label matched by the pattern, or None."""
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
            except re.error:
                return None
            return next((lb for lb in labels if compiled.fullmatch(lb)), None)

        matched_subs: list[tuple[Subscription, str]] = [
            (sub, matched)
            for sub in subs
            if (matched := first_match(sub.label, issue_labels)) is not None
        ]

        if not matched_subs:
            return {"ok": True, "skipped": "no matching subscriptions"}

        # Collect unique (user, matched_label) pairs to avoid double-notifying
        # the same user if they subscribed multiple matching labels.
        notified: set[int] = set()
        for sub, matched in matched_subs:
            if sub.user_id in notified:
                continue
            notified.add(sub.user_id)

            user: User = db.get(User, sub.user_id)
            if user is None:
                continue

            message = build_issue_message(issue, repo_full_name, matched)
            try:
                await send_dm(user.discord_id, message)
                logger.info("Notified user %s (discord: %s)", user.username, user.discord_id)
            except Exception as exc:
                logger.error("Failed to DM %s: %s", user.discord_id, exc)
    finally:
        db.close()

    return {"ok": True, "notified": len(notified)}
