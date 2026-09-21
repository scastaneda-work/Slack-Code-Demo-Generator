import logging
import os
import re
from datetime import datetime, timezone

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from agent.identity import resolve_user_name

logger = logging.getLogger(__name__)

# Slack user mention: <@U0123ABCD> (optionally <@U0123ABCD|label>). Left in an
# audit message these render as LIVE pings — noise for whoever's tagged every
# time the bot audits an action. We de-mention them to a plain, non-pinging
# "~Name" instead.
_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")


def _mask(message: str) -> str:
    """Mask a literal demo-admin name in prose to a non-pinging '~Name'.

    The name comes from the DEMO_ADMIN_NAME env var (e.g. the persona whose
    xoxp token seeds the demo); unset → no masking. Skips a name already
    prefixed with '~' so we don't double-tilde one produced by _demention."""
    name = os.environ.get("DEMO_ADMIN_NAME")
    if not name:
        return message
    pattern = re.compile(rf"(?<!~)\b{re.escape(name)}\b")
    return pattern.sub(f"~{name}", message)


async def _demention(client: AsyncWebClient, message: str) -> str:
    """Replace every `<@U…>` user mention with a non-pinging `~Name`.

    Audit lines routinely include `<@{user_id}>` to attribute an action; posted
    verbatim they ping that user. We resolve the id to a display name (cached via
    identity.resolve_user_name) and emit `~Name`, matching the existing ~Jennifer
    masking convention. Falls back to `~someone` if the name can't be resolved so
    the ping is neutralized regardless."""
    ids = set(_USER_MENTION_RE.findall(message))
    if not ids:
        return message
    for uid in ids:
        try:
            name = await resolve_user_name(client, uid)
        except Exception:  # noqa: BLE001 — never let de-mention break auditing
            name = None
        replacement = f"~{name}" if name else "~someone"
        # Replace both <@U…> and <@U…|label> forms for this id.
        message = re.sub(rf"<@{re.escape(uid)}(?:\|[^>]*)?>", replacement, message)
    return message


async def audit_log(client: AsyncWebClient, message: str) -> None:
    """Post a timestamped entry to the audit channel. Never raises.

    Reads AUDIT_CHANNEL_ID from env. If unset, silently no-ops (so dev runs
    without the env var don't break). Neutralizes user @-mentions to non-pinging
    `~Name` and masks the DEMO_ADMIN_NAME (if set) — audit noise should never
    ping a live demo user.
    """
    channel = os.environ.get("AUDIT_CHANNEL_ID")
    if not channel:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    body = f"`{timestamp}` {_mask(await _demention(client, message))}"
    try:
        await client.chat_postMessage(channel=channel, text=body)
    except SlackApiError as e:
        logger.warning("audit_log failed: %s — %s", e.response.get("error"), message)
