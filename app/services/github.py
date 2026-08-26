"""GitHub API helpers for update-based issue polling and validation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import regex as timeout_regex

from app.config import settings


GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL_API = "https://api.github.com/graphql"
_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Python's stdlib regex engine has no execution timeout. Reject the constructs
# most commonly used for catastrophic backtracking while retaining the simple
# wildcard patterns IssueBell documents (for example ``good.*issue``).
_REPEAT = r"(?:[+*]|\{\d+(?:,\d*)?\})"
_NESTED_REPEAT = re.compile(rf"\((?:\\.|[^()])*{_REPEAT}(?:\\.|[^()])*\){_REPEAT}")
_AMBIGUOUS_REPEAT = re.compile(rf"\((?:\\.|[^()])*\|(?:\\.|[^()])*\){_REPEAT}")
_STACKED_REPEAT = re.compile(rf"{_REPEAT}(?:[+*]|\{{)")
_BACKREFERENCE = re.compile(r"\\[1-9]")
LABEL_REGEX_TIMEOUT_SECONDS = 0.01


class GitHubAPIError(RuntimeError):
    """Base error with a stable code suitable for logs and API responses."""

    code = "github_api_error"
    http_status = 502
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        rate_limit_reset_at: datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.rate_limit_reset_at = rate_limit_reset_at


class GitHubAuthenticationError(GitHubAPIError):
    code = "github_authentication_error"
    http_status = 401
    retryable = False


class GitHubRateLimitError(GitHubAPIError):
    code = "github_rate_limit"
    http_status = 429
    retryable = True


class GitHubRepositoryNotFoundError(GitHubAPIError):
    code = "repository_not_found"
    http_status = 404
    retryable = False


class GitHubForbiddenError(GitHubAPIError):
    code = "github_forbidden"
    http_status = 403
    retryable = False


class GitHubTransientError(GitHubAPIError):
    code = "github_unavailable"
    http_status = 503
    retryable = True


class GitHubLabelNotFoundError(GitHubAPIError):
    code = "label_not_found"
    http_status = 422
    retryable = False

    def __init__(self, pattern: str, labels: list[str]) -> None:
        super().__init__(f"Label pattern {pattern!r} does not match any repository label")
        self.pattern = pattern
        self.labels = labels


def _headers(token: str) -> dict[str, str]:
    headers = dict(_GH_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_rate_limit_reset(response: httpx.Response) -> datetime | None:
    raw = response.headers.get("x-ratelimit-reset")
    if raw:
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OSError):
            pass
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
                seconds=max(0.0, float(retry_after))
            )
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def _raise_for_github_error(response: httpx.Response, repo: str) -> None:
    if response.is_success:
        return

    status = response.status_code
    try:
        api_message = str(response.json().get("message", ""))
    except (TypeError, ValueError):
        api_message = ""
    message = api_message[:300] or f"GitHub returned HTTP {status} for {repo}"
    reset_at = _parse_rate_limit_reset(response)
    remaining = response.headers.get("x-ratelimit-remaining")

    if status == 401:
        raise GitHubAuthenticationError("GitHub authorization is invalid or expired")
    if status == 404:
        raise GitHubRepositoryNotFoundError(
            f"Repository {repo!r} was not found or is not accessible"
        )
    if status == 429 or (
        status == 403
        and (remaining == "0" or "rate limit" in message.lower())
    ):
        raise GitHubRateLimitError(message, rate_limit_reset_at=reset_at)
    if status == 403:
        raise GitHubForbiddenError(message)
    if status >= 500:
        raise GitHubTransientError(message)
    raise GitHubAPIError(message)


async def _get(
    client: httpx.AsyncClient,
    url: str,
    *,
    repo: str,
    token: str,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    current_url = url
    current_params = params
    for redirect_count in range(4):
        try:
            response = await client.get(
                current_url,
                params=current_params,
                headers=_headers(token),
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise GitHubTransientError(f"GitHub request failed: {exc}") from exc

        if response.status_code not in {301, 302, 307, 308}:
            _raise_for_github_error(response, repo)
            return response

        if redirect_count == 3:
            raise GitHubAPIError(f"GitHub returned too many redirects for {repo}")
        location = response.headers.get("location")
        if not location:
            raise GitHubAPIError(f"GitHub returned an invalid redirect for {repo}")
        redirected_url = urljoin(str(response.url), location)
        target = urlparse(redirected_url)
        if target.scheme != "https" or target.hostname != "api.github.com":
            raise GitHubAPIError(f"GitHub returned an unsafe redirect for {repo}")
        current_url = redirected_url
        if target.query:
            current_params = None

    raise GitHubAPIError(f"GitHub redirect handling failed for {repo}")


async def _paginated_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    repo: str,
    token: str,
    params: dict[str, Any] | None = None,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_url: str | None = url
    next_params = params
    visited_urls: set[str] = set()
    page_count = 0

    while next_url:
        if next_url in visited_urls:
            raise GitHubAPIError(f"GitHub returned a pagination loop for {repo}")
        if page_count >= max_pages:
            raise GitHubAPIError(
                f"GitHub pagination exceeded the {max_pages}-page safety limit for {repo}"
            )
        visited_urls.add(next_url)
        page_count += 1
        response = await _get(
            client,
            next_url,
            repo=repo,
            token=token,
            params=next_params,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubAPIError(f"GitHub returned invalid JSON for {repo}") from exc
        if not isinstance(payload, list):
            raise GitHubAPIError(f"GitHub returned an invalid list response for {repo}")
        items.extend(item for item in payload if isinstance(item, dict))

        next_link = response.links.get("next", {}).get("url")
        next_url = str(next_link) if next_link else None
        next_params = None

    return items


async def fetch_new_issues(
    repo: str,
    token: str,
    since: datetime | None,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Return open issues updated since ``since``.

    Polling by ``updated_at`` (rather than ``created_at``) is intentional: an
    issue that receives a matching label or becomes unassigned after it was
    opened must be evaluated again. The caller verifies the corresponding issue
    event before enqueueing a notification.
    """

    params: dict[str, Any] = {
        "state": "open",
        "per_page": 100,
        "sort": "updated",
        "direction": "asc",
    }
    if since:
        since_utc = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
        # Overlap the cursor because GitHub may exclude updates exactly at the
        # stored second. Durable delivery idempotency makes re-fetching safe.
        since_utc -= timedelta(seconds=2)
        params["since"] = since_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=settings.outbound_http_timeout)
    try:
        raw_issues = await _paginated_json(
            client,
            f"{GITHUB_API}/repos/{repo}/issues",
            repo=repo,
            token=token,
            params=params,
        )
    finally:
        if owns_client:
            await client.aclose()

    deduplicated: dict[str, dict[str, Any]] = {}
    for issue in raw_issues:
        if "pull_request" in issue:
            continue
        identity = str(issue.get("id") or issue.get("node_id") or issue.get("number"))
        deduplicated[identity] = issue

    return sorted(
        deduplicated.values(),
        key=lambda issue: issue.get("updated_at") or issue.get("created_at") or "",
    )


async def fetch_recent_issues_limited(
    repo: str,
    token: str,
    since: datetime,
    *,
    limit: int = 300,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Return a bounded set of recently updated open issues for previews.

    The boolean result is true when GitHub reported another page. Preview calls
    must stay bounded even for very busy repositories, while the polling path
    remains exhaustive and checkpoint-safe.
    """

    safe_limit = min(max(limit, 1), 500)
    since_utc = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
    params: dict[str, Any] | None = {
        "state": "open",
        "per_page": min(100, safe_limit),
        "sort": "updated",
        "direction": "desc",
        "since": since_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    next_url: str | None = f"{GITHUB_API}/repos/{repo}/issues"
    raw_issues: list[dict[str, Any]] = []
    visited_urls: set[str] = set()
    has_more = False

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=settings.outbound_http_timeout)
    try:
        while next_url and len(raw_issues) < safe_limit:
            if next_url in visited_urls:
                raise GitHubAPIError(f"GitHub returned a pagination loop for {repo}")
            visited_urls.add(next_url)
            response = await _get(
                client,
                next_url,
                repo=repo,
                token=token,
                params=params,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise GitHubAPIError(f"GitHub returned invalid JSON for {repo}") from exc
            if not isinstance(payload, list):
                raise GitHubAPIError(f"GitHub returned an invalid list response for {repo}")
            raw_issues.extend(item for item in payload if isinstance(item, dict))
            next_link = response.links.get("next", {}).get("url")
            next_url = str(next_link) if next_link else None
            params = None
        has_more = next_url is not None or len(raw_issues) > safe_limit
    finally:
        if owns_client:
            await client.aclose()

    deduplicated: dict[str, dict[str, Any]] = {}
    for issue in raw_issues[:safe_limit]:
        if "pull_request" in issue:
            continue
        identity = str(issue.get("id") or issue.get("node_id") or issue.get("number"))
        deduplicated[identity] = issue

    issues = sorted(
        deduplicated.values(),
        key=lambda issue: issue.get("updated_at") or issue.get("created_at") or "",
        reverse=True,
    )
    return issues, has_more


async def fetch_issue_events(
    repo: str,
    issue_numbers: list[int],
    token: str,
    since: datetime,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, str]]:
    """Return recent actionable events for several issues in bounded batches.

    GraphQL's ``timelineItems(since:)`` avoids replaying the complete history of
    old issues. Field-level failures are returned per issue so one unavailable
    timeline cannot block the rest of a repository poll.
    """

    if not issue_numbers:
        return {}, {}
    owner, name = repo.split("/", 1)
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=settings.outbound_http_timeout)

    events_by_issue: dict[int, list[dict[str, Any]]] = {}
    failures: dict[int, str] = {}
    since_utc = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
    since_value = (since_utc - timedelta(seconds=2)).astimezone(timezone.utc).isoformat()
    try:
        for offset in range(0, len(issue_numbers), 25):
            batch = list(dict.fromkeys(issue_numbers[offset : offset + 25]))
            aliases = "\n".join(
                f"issue_{number}: issue(number: {number}) {{ ...ActionableEvents }}"
                for number in batch
            )
            query = f"""
                query($owner: String!, $name: String!, $since: DateTime!) {{
                  repository(owner: $owner, name: $name) {{
                    {aliases}
                  }}
                }}
                fragment ActionableEvents on Issue {{
                  timelineItems(
                    first: 100
                    since: $since
                    itemTypes: [LABELED_EVENT, UNASSIGNED_EVENT, REOPENED_EVENT]
                  ) {{
                    nodes {{
                      __typename
                      ... on LabeledEvent {{ id createdAt label {{ name }} }}
                      ... on UnassignedEvent {{ id createdAt }}
                      ... on ReopenedEvent {{ id createdAt }}
                    }}
                    pageInfo {{ hasNextPage endCursor }}
                  }}
                }}
            """
            payload = await _graphql_request(
                client,
                query,
                variables={"owner": owner, "name": name, "since": since_value},
                repo=repo,
                token=token,
            )
            data = payload.get("data")
            repository = data.get("repository") if isinstance(data, dict) else None
            if not isinstance(repository, dict):
                raise GitHubRepositoryNotFoundError(
                    f"Repository {repo!r} was not found or is not accessible"
                )

            batch_failures = _graphql_issue_failures(payload, batch)
            if any(
                not _graphql_error_has_issue_path(error)
                for error in payload.get("errors", [])
                if isinstance(error, dict)
            ):
                raise GitHubAPIError(
                    f"GitHub returned an unscoped GraphQL error for {repo}"
                )
            failures.update(batch_failures)
            for number in batch:
                if number in batch_failures:
                    continue
                issue_data = repository.get(f"issue_{number}")
                if not isinstance(issue_data, dict):
                    failures[number] = "Issue timeline is unavailable"
                    continue
                timeline = issue_data.get("timelineItems")
                if not isinstance(timeline, dict):
                    failures[number] = "Issue timeline is unavailable"
                    continue
                events_by_issue[number] = _normalize_graphql_events(
                    timeline.get("nodes")
                )
                page_info = timeline.get("pageInfo")
                if isinstance(page_info, dict) and page_info.get("hasNextPage"):
                    more_events = await _fetch_remaining_issue_events(
                        client,
                        repo=repo,
                        owner=owner,
                        name=name,
                        issue_number=number,
                        token=token,
                        since=since_value,
                        after=str(page_info.get("endCursor") or ""),
                    )
                    events_by_issue[number].extend(more_events)
    finally:
        if owns_client:
            await client.aclose()

    return events_by_issue, failures


async def _graphql_request(
    client: httpx.AsyncClient,
    query: str,
    *,
    variables: dict[str, Any],
    repo: str,
    token: str,
) -> dict[str, Any]:
    try:
        response = await client.post(
            GITHUB_GRAPHQL_API,
            headers=_headers(token),
            json={"query": query, "variables": variables},
        )
    except httpx.RequestError as exc:
        raise GitHubTransientError(f"GitHub GraphQL request failed: {exc}") from exc
    _raise_for_github_error(response, repo)
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitHubAPIError(f"GitHub returned invalid GraphQL JSON for {repo}") from exc
    if not isinstance(payload, dict):
        raise GitHubAPIError(f"GitHub returned an invalid GraphQL response for {repo}")
    error_messages = [
        str(error.get("message", ""))
        for error in payload.get("errors", [])
        if isinstance(error, dict)
    ]
    if any("rate limit" in message.casefold() for message in error_messages):
        raise GitHubRateLimitError(
            error_messages[0] or "GitHub GraphQL rate limit exceeded",
            rate_limit_reset_at=_parse_rate_limit_reset(response),
        )
    if any("authentication" in message.casefold() for message in error_messages):
        raise GitHubAuthenticationError("GitHub authorization is invalid or expired")
    return payload


def _graphql_issue_failures(
    payload: dict[str, Any], issue_numbers: list[int]
) -> dict[int, str]:
    failures: dict[int, str] = {}
    valid_numbers = set(issue_numbers)
    for error in payload.get("errors", []):
        if not isinstance(error, dict):
            continue
        path = error.get("path")
        if not isinstance(path, list):
            continue
        alias = next(
            (part for part in path if isinstance(part, str) and part.startswith("issue_")),
            None,
        )
        if alias is None:
            continue
        try:
            number = int(alias.removeprefix("issue_"))
        except ValueError:
            continue
        if number in valid_numbers:
            failures[number] = str(error.get("message") or "Issue timeline is unavailable")[:300]
    return failures


def _graphql_error_has_issue_path(error: dict[str, Any]) -> bool:
    path = error.get("path")
    return isinstance(path, list) and any(
        isinstance(part, str) and part.startswith("issue_") for part in path
    )


def _normalize_graphql_events(nodes: object) -> list[dict[str, Any]]:
    if not isinstance(nodes, list):
        return []
    event_names = {
        "LabeledEvent": "labeled",
        "UnassignedEvent": "unassigned",
        "ReopenedEvent": "reopened",
    }
    events: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        event_name = event_names.get(str(node.get("__typename")))
        if event_name is None:
            continue
        event = {
            "id": node.get("id"),
            "event": event_name,
            "created_at": node.get("createdAt"),
        }
        if event_name == "labeled" and isinstance(node.get("label"), dict):
            event["label"] = {"name": str(node["label"].get("name", ""))}
        events.append(event)
    return events


async def _fetch_remaining_issue_events(
    client: httpx.AsyncClient,
    *,
    repo: str,
    owner: str,
    name: str,
    issue_number: int,
    token: str,
    since: str,
    after: str,
) -> list[dict[str, Any]]:
    query = """
        query(
          $owner: String!, $name: String!, $number: Int!,
          $since: DateTime!, $after: String!
        ) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) {
              timelineItems(
                first: 100
                after: $after
                since: $since
                itemTypes: [LABELED_EVENT, UNASSIGNED_EVENT, REOPENED_EVENT]
              ) {
                nodes {
                  __typename
                  ... on LabeledEvent { id createdAt label { name } }
                  ... on UnassignedEvent { id createdAt }
                  ... on ReopenedEvent { id createdAt }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
    """
    events: list[dict[str, Any]] = []
    cursor = after
    for _ in range(10):
        payload = await _graphql_request(
            client,
            query,
            variables={
                "owner": owner,
                "name": name,
                "number": issue_number,
                "since": since,
                "after": cursor,
            },
            repo=repo,
            token=token,
        )
        if payload.get("errors"):
            raise GitHubAPIError(
                f"GitHub could not finish event pagination for {repo}#{issue_number}"
            )
        data = payload.get("data")
        repository = data.get("repository") if isinstance(data, dict) else None
        issue_data = repository.get("issue") if isinstance(repository, dict) else None
        if not isinstance(issue_data, dict):
            return events
        timeline = issue_data.get("timelineItems")
        if not isinstance(timeline, dict):
            return events
        events.extend(_normalize_graphql_events(timeline.get("nodes")))
        page_info = timeline.get("pageInfo")
        if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
            return events
        cursor = str(page_info.get("endCursor") or "")
        if not cursor:
            return events
    raise GitHubAPIError(
        f"GitHub returned more than 1,100 actionable events for {repo}#{issue_number}"
    )


async def fetch_repository_labels(
    repo: str,
    token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate repository access and return all configured labels."""

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=settings.outbound_http_timeout)
    try:
        repository_response = await _get(
            client,
            f"{GITHUB_API}/repos/{repo}",
            repo=repo,
            token=token,
        )
        try:
            repository = repository_response.json()
        except ValueError as exc:
            raise GitHubAPIError(
                f"GitHub returned invalid repository metadata for {repo}"
            ) from exc
        if not isinstance(repository, dict):
            raise GitHubAPIError(f"GitHub returned invalid repository metadata for {repo}")

        labels = await _paginated_json(
            client,
            f"{GITHUB_API}/repos/{repo}/labels",
            repo=repo,
            token=token,
            params={"per_page": 100},
            max_pages=10,
        )
    finally:
        if owns_client:
            await client.aclose()

    return repository, labels


async def validate_repository_label(
    repo: str,
    pattern: str,
    token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Require an accessible repository and at least one matching label."""

    repository, labels = await fetch_repository_labels(repo, token, client=client)
    label_names = [str(label.get("name", "")) for label in labels]
    if match_label(pattern, label_names) is None:
        raise GitHubLabelNotFoundError(pattern, label_names)
    return repository, labels


def _parse_gh_dt(dt_str: str) -> datetime:
    """Parse a GitHub ISO-8601 timestamp to naive UTC."""

    return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def compile_label_pattern(pattern: str) -> timeout_regex.Pattern:
    """Compile a limited regex whose matches are protected by a hard timeout."""

    if (
        _NESTED_REPEAT.search(pattern)
        or _AMBIGUOUS_REPEAT.search(pattern)
        or _STACKED_REPEAT.search(pattern)
        or _BACKREFERENCE.search(pattern)
    ):
        raise ValueError(
            "Unsafe regular expression: nested, ambiguous, stacked repeats and "
            "backreferences are not supported"
        )
    try:
        return timeout_regex.compile(pattern, timeout_regex.IGNORECASE)
    except timeout_regex.error as exc:
        raise ValueError("Invalid regular expression") from exc


def match_label(pattern: str, issue_labels: list[str]) -> str | None:
    """Return the first label name that fully matches a case-insensitive regex."""

    compiled = compile_label_pattern(pattern)
    for label in issue_labels:
        try:
            if compiled.fullmatch(label, timeout=LABEL_REGEX_TIMEOUT_SECONDS):
                return label
        except TimeoutError as exc:
            raise ValueError(
                "Label regular expression exceeded the safe execution time"
            ) from exc
    return None


def build_issue_message(
    issue: dict[str, Any],
    repo: str,
    matched_label: str,
    trigger_reason: str = "created",
) -> str:
    def escape_markdown(value: object) -> str:
        return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|])", r"\\\1", str(value))

    title = escape_markdown(issue.get("title") or "(no title)")
    raw_number = issue.get("number")
    number = raw_number if isinstance(raw_number, int) else "?"
    raw_user = issue.get("user")
    author = escape_markdown(
        raw_user.get("login", "unknown") if isinstance(raw_user, dict) else "unknown"
    )
    raw_labels = issue.get("labels")
    labels = (
        ", ".join(
            f"`{escape_markdown(label.get('name') or '')}`"
            for label in raw_labels
            if isinstance(label, dict)
        )
        if isinstance(raw_labels, list)
        else ""
    )
    raw_assignees = issue.get("assignees")
    assignee_names = (
        [
            escape_markdown(assignee.get("login") or "unknown")
            for assignee in raw_assignees
            if isinstance(assignee, dict)
        ]
        if isinstance(raw_assignees, list)
        else []
    )
    if not assignee_names and isinstance(issue.get("assignee"), dict):
        assignee_names = [escape_markdown(issue["assignee"].get("login") or "unknown")]

    safe_matched_label = escape_markdown(matched_label)
    reason = {
        "label_added": f"`{safe_matched_label}` was just added",
        "unassigned": "The issue just became unassigned",
        "reopened": "The issue was reopened and is unassigned",
        "created": "New unassigned issue matching your watch",
    }.get(trigger_reason, "The issue became actionable")
    assignment = (
        f"Assigned to **{', '.join(assignee_names)}**"
        if assignee_names
        else "Unassigned"
    )
    # Build a trusted GitHub link instead of embedding provider-supplied URL
    # text that could turn IssueBell DMs into phishing links.
    url = f"https://github.com/{repo}/issues/{number}" if number != "?" else ""

    return (
        f"\U0001f514 **Issue alert on `{repo}`**\n"
        f"**#{number} \u2014 {title}**\n"
        f"\u2728 {reason}\n"
        f"\U0001f464 Opened by **{author}**\n"
        f"\U0001f465 {assignment}\n"
        f"\U0001f3f7\ufe0f Labels: {labels or '\u2014'}\n"
        f"\U0001f517 {url}"
    )
