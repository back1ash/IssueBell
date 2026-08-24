from datetime import datetime

import httpx
import pytest

from app.services.github import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubRateLimitError,
    build_issue_message,
    compile_label_pattern,
    fetch_new_issues,
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
