"""Discord bot helper — sends DMs to users."""

import httpx

from app.config import settings

DISCORD_API = "https://discord.com/api/v10"


async def _bot_headers() -> dict:
    return {"Authorization": f"Bot {settings.discord_bot_token}"}


async def open_dm_channel(discord_user_id: str) -> str:
    """Create (or retrieve) a DM channel with a Discord user. Returns the channel id."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{DISCORD_API}/users/@me/channels",
            json={"recipient_id": discord_user_id},
            headers=await _bot_headers(),
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def send_dm(discord_user_id: str, content: str) -> None:
    """Send a direct message to a Discord user via the bot."""
    channel_id = await open_dm_channel(discord_user_id)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            json={"content": content},
            headers=await _bot_headers(),
        )
        resp.raise_for_status()


def build_issue_message(issue: dict, repo: str, matched_label: str) -> str:
    """Format the Discord DM message for a new issue notification."""
    title = issue.get("title", "(no title)")
    url = issue.get("html_url", "")
    number = issue.get("number", "?")
    author = issue.get("user", {}).get("login", "unknown")
    labels = ", ".join(lb["name"] for lb in issue.get("labels", []))

    return (
        f"🔔 **New issue in `{repo}`** (matched label: `{matched_label}`)\n"
        f"**#{number} — {title}**\n"
        f"👤 Opened by **{author}**\n"
        f"🏷️ Labels: {labels or '—'}\n"
        f"🔗 {url}"
    )
