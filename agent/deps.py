from dataclasses import dataclass

from slack_sdk.web.async_client import AsyncWebClient


@dataclass
class AgentDeps:
    client: AsyncWebClient
    user_id: str
    channel_id: str
    # None for a code-channel session (reply top-level, omit thread_ts per the
    # Slack Code convention); a real ts for a threaded/DM reply.
    thread_ts: str | None
    message_ts: str
    user_token: str | None = None
