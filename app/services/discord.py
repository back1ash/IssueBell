"""Discord bot helper — sends DMs to users with bounded retries."""

import asyncio
import random

import httpx

from app.config import settings

DISCORD_API = "https://discord.com/api/v10"
MAX_MESSAGE_LENGTH = 2000
MAX_ATTEMPTS = 3


class DiscordDeliveryError(RuntimeError):
    """A Discord API operation failed after bounded retry attempts."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


async def _bot_headers() -> dict:
    return {"Authorization": f"Bot {settings.discord_bot_token}"}


async def _request_with_retry(
    method: str,
    path: str,
    *,
    json: dict,
) -> httpx.Response:
    timeout = getattr(settings, "outbound_http_timeout", 15.0)
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await client.request(
                    method,
                    f"{DISCORD_API}{path}",
                    json=json,
                    headers=await _bot_headers(),
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    return response
                if response.status_code == 429:
                    try:
                        retry_after = float(response.json().get("retry_after", 1.0))
                    except (TypeError, ValueError):
                        retry_after = 1.0
                    delay = min(max(retry_after, 0.1), 5.0)
                    last_error = DiscordDeliveryError("Discord rate limit exceeded")
                elif response.status_code >= 500:
                    delay = min(0.5 * (2**attempt) + random.random() * 0.2, 3.0)
                    last_error = DiscordDeliveryError(
                        f"Discord temporarily unavailable ({response.status_code})"
                    )
                else:
                    raise DiscordDeliveryError(
                        f"Discord rejected the request ({response.status_code})",
                        # 401 usually means an operator rotated/misconfigured
                        # the bot token and must not permanently discard every
                        # queued user notification.
                        retryable=response.status_code == 401,
                        status_code=response.status_code,
                    )

                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(delay)
                    continue

            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(min(0.5 * (2**attempt), 2.0))

    raise DiscordDeliveryError("Discord request failed after retries") from last_error


async def open_dm_channel(discord_user_id: str) -> str:
    """Create (or retrieve) a DM channel with a Discord user. Returns the channel id."""
    resp = await _request_with_retry(
        "POST",
        "/users/@me/channels",
        json={"recipient_id": discord_user_id},
    )
    return resp.json()["id"]


async def send_dm(discord_user_id: str, content: str) -> None:
    """Send a direct message to a Discord user via the bot."""
    channel_id = await open_dm_channel(discord_user_id)
    safe_content = content[:MAX_MESSAGE_LENGTH]
    await _request_with_retry(
        "POST",
        f"/channels/{channel_id}/messages",
        json={
            "content": safe_content,
            # GitHub-controlled titles, usernames, and labels must never create
            # mentions when rendered by Discord.
            "allowed_mentions": {"parse": []},
        },
    )


def build_welcome_message(username: str) -> str:
    """Format the DM sent after a user completes their first Discord sign-in."""
    return (
        f"👋 **Welcome to IssueBell, {username}!**\n"
        "Your Discord account is connected and ready to receive alerts.\n\n"
        "Next, connect GitHub and add a repository label filter. "
        "When a matching issue is found, I'll send it here within minutes. 🔔"
    )
