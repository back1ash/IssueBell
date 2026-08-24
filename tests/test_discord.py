import pytest

from app.services import discord


@pytest.mark.asyncio
async def test_send_dm_disables_mentions_and_bounds_message(monkeypatch):
    captured: dict = {}

    async def fake_open_dm_channel(user_id: str) -> str:
        assert user_id == "discord-user"
        return "channel-id"

    async def fake_request(method: str, path: str, *, json: dict):
        captured.update({"method": method, "path": path, "json": json})

    monkeypatch.setattr(discord, "open_dm_channel", fake_open_dm_channel)
    monkeypatch.setattr(discord, "_request_with_retry", fake_request)

    await discord.send_dm("discord-user", "@everyone " + "x" * 2500)

    assert captured["method"] == "POST"
    assert captured["path"] == "/channels/channel-id/messages"
    assert len(captured["json"]["content"]) == discord.MAX_MESSAGE_LENGTH
    assert captured["json"]["allowed_mentions"] == {"parse": []}
