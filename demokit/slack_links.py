"""Find a demo channel in Slack so the UI can open it (read-only lookup)."""
from __future__ import annotations


def channel_url(name: str) -> str | None:
    """A slack:// link that opens #name in the desktop app, or None if no
    channel by that name exists. Uses the bot token; raises on API errors."""
    from config import app_client, load_tokens

    team_id = load_tokens().get("team_id") or None
    client = app_client()
    cursor = None
    while True:
        page = client.conversations_list(types="public_channel,private_channel", limit=1000,
                                         exclude_archived=True, cursor=cursor, team_id=team_id)
        for c in page["channels"]:
            if c["name"] == name:
                team = team_id or c.get("context_team_id") or ""
                return f"slack://channel?team={team}&id={c['id']}"
        cursor = page.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return None
