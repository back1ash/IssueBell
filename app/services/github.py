"""GitHub API helpers  polling-based issue detection."""

import hashlib
import hmac
import re
from datetime import datetime, timezone

import httpx

from app.config import settings


def verify_signature(body: bytes, signature_header: str | None) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature.

    Returns True when the signature matches or when no webhook secret is
    configured (dev/test mode).  Returns False on mismatch.
    """
    secret = settings.github_webhook_secret
    if not secret:
        # No secret configured — skip verification (dev mode).
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)

GITHUB_API = "https://api.github.com"
_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


async def fetch_new_issues(
    repo: str,
    token: str,
    since: datetime | None,
) -> list[dict]:
    """Return issues (not PRs) in *repo* created after *since*.

    Uses the authenticated user's token so rate-limit is per-user (5 000 req/hr).
    *since* is compared against created_at, not updated_at.
    """
    params: dict = {
        "state": "open",
        "per_page": 50,
        "sort": "created",
        "direction": "desc",
    }
    if since:
        # GitHub `since` filters by updated_at, so we pass it but re-filter by created_at below.
        since_utc = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
        params["since"] = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    headers = {**_GH_HEADERS, "Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GITHUB_API}/repos/{repo}/issues",
            params=params,
            headers=headers,
        )
        if resp.status_code in (404, 403, 401):
            return []
        resp.raise_for_status()
        issues: list[dict] = resp.json()

    # Strip pull requests (GitHub issues endpoint returns them too)
    issues = [i for i in issues if "pull_request" not in i]

    # Re-filter to created_at > since (the API filters by updated_at)
    if since:
        since_naive = since.replace(tzinfo=None)
        issues = [
            i for i in issues
            if _parse_gh_dt(i["created_at"]) > since_naive
        ]

    return issues


def _parse_gh_dt(dt_str: str) -> datetime:
    """Parse GitHub ISO-8601 timestamp to a naive UTC datetime."""
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).replace(tzinfo=None)


def match_label(pattern: str, issue_labels: list[str]) -> str | None:
    """Return the first label name that matches the regex pattern, or None."""
    for label in issue_labels:
        if re.fullmatch(pattern, label, re.IGNORECASE):
            return label
    return None


def build_issue_message(issue: dict, repo: str, matched_label: str) -> str:
    title  = issue.get("title", "(no title)")
    url    = issue.get("html_url", "")
    number = issue.get("number", "?")
    author = issue.get("user", {}).get("login", "unknown")
    labels = ", ".join(f"`{lb['name']}`" for lb in issue.get("labels", []))

    return (
        f"\U0001f514 **New issue on `{repo}`**\n"
        f"**#{number} \u2014 {title}**\n"
        f"\U0001f464 Opened by **{author}**\n"
        f"\U0001f3f7\ufe0f Labels: {labels or '\u2014'}\n"
        f"\U0001f517 {url}"
    )
