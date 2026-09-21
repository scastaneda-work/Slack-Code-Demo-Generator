import logging
import re

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)

_NAME_BAD_CHARS = re.compile(r"[`*_~|<>\\\n\r\t]")

_user_name_cache: dict[str, str | None] = {}


def _sanitize_user_name(name: str) -> str:
    """Strip Slack mrkdwn-control chars and cap length so a pasted/styled
    display name can't act as prompt injection or dominate the system prompt."""
    cleaned = _NAME_BAD_CHARS.sub(" ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80]


async def resolve_user_name(client: AsyncWebClient, user_id: str) -> str | None:
    """Return a user's display name, cached per user_id. Prefers
    profile.display_name, then profile.real_name, then user.real_name.
    Returns None on any failure or when all fields are empty."""
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]
    try:
        resp = await client.users_info(user=user_id)
    except SlackApiError as e:
        logger.warning("users.info failed for %s: %s", user_id, e.response.get("error"))
        _user_name_cache[user_id] = None
        return None
    user = resp.get("user") or {}
    profile = user.get("profile") or {}
    raw = (
        profile.get("display_name")
        or profile.get("real_name")
        or user.get("real_name")
        or ""
    )
    name = _sanitize_user_name(raw) or None
    _user_name_cache[user_id] = name
    return name
