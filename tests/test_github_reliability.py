import json
from datetime import datetime

import httpx
import pytest

from app.services.github import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubRateLimitError,
    build_issue_message,
    compile_label_pattern,
    fetch_issue_events,
    fetch_new_issues,
    fetch_recent_issues_limited,
    match_label,
)


@pytest.mark.asyncio
async def test_fetch_new_issues_uses_updates_and_follows_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 2,
                        "number": 2,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2026-08-23T11:01:00Z",
                        "labels": [{"name": "good first issue"}],
                    },
                    {
                        "id": 3,
                        "number": 3,
                        "updated_at": "2026-08-23T11:02:00Z",
                        "pull_request": {"url": "https://api.github.test/pulls/3"},
                    },
                ],
            )
        return httpx.Response(
            200,
            headers={
                "Link": '<https://api.github.test/repos/acme/project/issues?page=2>; rel="next"'
            },
            json=[
                {
                    "id": 1,
                    "number": 1,
                    "created_at": "2026-08-23T10:59:00Z",
                    "updated_at": "2026-08-23T11:00:30Z",
                    "labels": [],
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        issues = await fetch_new_issues(
            "acme/project",
            "token",
            datetime(2026, 8, 23, 11, 0, 0),
            client=client,
        )

    assert [issue["id"] for issue in issues] == [1, 2]
    # Issue 2 was created years ago but is returned because its labels changed now.
    assert issues[1]["created_at"].startswith("2024")
    assert requests[0].url.params["sort"] == "updated"
    assert requests[0].url.params["direction"] == "asc"
    assert requests[0].url.params["per_page"] == "100"
    assert requests[0].url.params["since"] == "2026-08-23T10:59:58Z"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_preview_issue_fetch_is_bounded_and_reports_more_pages() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "Link": '<https://api.github.com/repos/acme/project/issues?page=2>; rel="next"'
            },
            json=[
                {
                    "id": 10,
                    "number": 10,
                    "created_at": "2026-08-25T10:00:00Z",
                    "updated_at": "2026-08-25T11:00:00Z",
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        issues, has_more = await fetch_recent_issues_limited(
            "acme/project",
            "token",
            datetime(2026, 7, 27, 0, 0, 0),
            limit=1,
            client=client,
        )

    assert [issue["number"] for issue in issues] == [10]
    assert has_more is True
    assert requests[0].url.params["direction"] == "desc"
    assert requests[0].url.params["per_page"] == "1"
    assert requests[0].url.params["since"] == "2026-07-27T00:00:00Z"


@pytest.mark.asyncio
async def test_fetch_issue_events_batches_recent_actionable_timeline_items() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "issue_42": {
                            "timelineItems": {
                                "nodes": [
                                    {
                                        "__typename": "LabeledEvent",
                                        "id": "LE_901",
                                        "createdAt": "2026-08-23T11:00:00Z",
                                        "label": {"name": "help needed"},
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False, "endCursor": "end"},
                            }
                        }
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events_by_issue, failures = await fetch_issue_events(
            "acme/project",
            [42],
            "token",
            datetime(2026, 8, 23, 10, 59, 0),
            client=client,
        )

    assert failures == {}
    assert events_by_issue[42] == [
        {
            "id": "LE_901",
            "event": "labeled",
            "created_at": "2026-08-23T11:00:00Z",
            "label": {"name": "help needed"},
        }
    ]
    assert requests[0].url.path == "/graphql"
    body = json.loads(requests[0].content)
    assert "issue_42: issue(number: 42)" in body["query"]
    assert body["variables"]["since"] == "2026-08-23T10:58:58+00:00"


@pytest.mark.asyncio
async def test_fetch_issue_events_isolates_one_graphql_field_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "issue_42": {
                            "timelineItems": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        },
                        "issue_43": None,
                    }
                },
                "errors": [
                    {
                        "message": "Timeline is gone",
                        "path": ["repository", "issue_43", "timelineItems"],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events_by_issue, failures = await fetch_issue_events(
            "acme/project",
            [42, 43],
            "token",
            datetime(2026, 8, 23, 10, 59, 0),
            client=client,
        )

    assert events_by_issue == {42: []}
    assert failures == {43: "Timeline is gone"}


@pytest.mark.asyncio
async def test_fetch_issue_events_paginates_only_recent_actionable_events() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        if len(requests) == 1:
            issue_data = {
                "issue_42": {
                    "timelineItems": {
                        "nodes": [
                            {
                                "__typename": "UnassignedEvent",
                                "id": "UE_1",
                                "createdAt": "2026-08-23T11:00:00Z",
                            }
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    }
                }
            }
        else:
            assert body["variables"]["after"] == "cursor-1"
            issue_data = {
                "issue": {
                    "timelineItems": {
                        "nodes": [
                            {
                                "__typename": "ReopenedEvent",
                                "id": "RE_2",
                                "createdAt": "2026-08-23T11:01:00Z",
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": "cursor-2"},
                    }
                }
            }
        return httpx.Response(200, json={"data": {"repository": issue_data}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events_by_issue, failures = await fetch_issue_events(
            "acme/project",
            [42],
            "token",
            datetime(2026, 8, 23, 10, 59, 0),
            client=client,
        )

    assert failures == {}
    assert [event["event"] for event in events_by_issue[42]] == [
        "unassigned",
        "reopened",
    ]
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_fetch_issue_events_surfaces_graphql_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1787472000"},
            json={"data": None, "errors": [{"message": "API rate limit exceeded"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubRateLimitError):
            await fetch_issue_events(
                "acme/project",
                [42],
                "token",
                datetime(2026, 8, 23, 10, 59, 0),
                client=client,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "error_type"),
    [
        (401, {}, GitHubAuthenticationError),
        (403, {"X-RateLimit-Remaining": "0"}, GitHubRateLimitError),
        (429, {}, GitHubRateLimitError),
    ],
)
async def test_fetch_errors_are_distinct(
    status: int,
    headers: dict[str, str],
    error_type: type[Exception],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, json={"message": "failed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(error_type):
            await fetch_new_issues("acme/project", "token", None, client=client)


def test_dangerous_nested_regex_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsafe regular expression"):
        compile_label_pattern("(a+)+$")

    assert compile_label_pattern("good.*issue").fullmatch("good first issue")


def test_ambiguous_regex_is_bounded_by_timeout() -> None:
    with pytest.raises(ValueError, match="safe execution time"):
        match_label(r"(?:(?:a|aa))*$", ["a" * 500 + "!"])


@pytest.mark.asyncio
async def test_github_redirects_are_limited_to_official_api_host() -> None:
    redirected_requests: list[httpx.Request] = []

    def safe_handler(request: httpx.Request) -> httpx.Response:
        redirected_requests.append(request)
        if request.url.path == "/repos/acme/old/issues":
            return httpx.Response(
                301,
                headers={"Location": "https://api.github.com/repos/acme/new/issues"},
            )
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(safe_handler)) as client:
        assert await fetch_new_issues("acme/old", "token", None, client=client) == []
    assert redirected_requests[1].url.params["state"] == "open"
    assert redirected_requests[1].url.params["per_page"] == "100"

    def unsafe_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example/issues"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(unsafe_handler)) as client:
        with pytest.raises(GitHubAPIError, match="unsafe redirect"):
            await fetch_new_issues("acme/old", "token", None, client=client)


def test_issue_message_escapes_markdown_and_handles_nullable_author() -> None:
    message = build_issue_message(
        {
            "number": 42,
            "title": "[Reconnect](https://evil.example) *now*",
            "html_url": "https://evil.example/phish",
            "user": None,
            "labels": [{"name": "`urgent`"}, None],
        },
        "acme/project",
        "urgent",
    )

    assert "https://evil.example" not in message
    assert r"\[Reconnect\]\(https://evil\.example\)" in message
    assert "Opened by **unknown**" in message
    assert "https://github.com/acme/project/issues/42" in message


def test_invalid_regex_is_reported_as_a_value_error() -> None:
    with pytest.raises(ValueError, match="Invalid regular expression"):
        compile_label_pattern("[")
