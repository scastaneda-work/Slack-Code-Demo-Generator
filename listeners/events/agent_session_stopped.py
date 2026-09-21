"""Handle the Slack Code `agent_session_stopped` event.

Slack fires this when a user cancels the agent (or Slack terminates a runaway
session) in a code channel. On receipt the app must immediately stop work in
that channel/thread and post nothing further, and must NOT call any
agents.sessions.* / agents.conversations.* methods for it (Slack updates the
session status itself).

We record the stop in the cooperative registry (`agent.slackcode`); the session
orchestrator checks it between steps and bails. This event was previously named
`message_stream_stopped`; subscribe to `agent_session_stopped` (Slack fires both
during the rename rollout).
"""
from __future__ import annotations

from logging import Logger

from slack_sdk.web.async_client import AsyncWebClient

from agent import slackcode
from agent.audit import audit_log


async def handle_agent_session_stopped(
    client: AsyncWebClient, event: dict, logger: Logger
):
    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts")
    if not channel_id:
        return
    slackcode.mark_session_stopped(channel_id)
    logger.info(
        "agent_session_stopped: channel=%s thread=%s — halting session",
        channel_id, thread_ts,
    )
    # Do NOT post to the channel or call agents.sessions.*/agents.conversations.*
    # here — Slack updates the session status on its own. Just audit it.
    await audit_log(
        client,
        f":octagonal_sign: Claude AI session stopped in <#{channel_id}>"
        + (f" (thread `{thread_ts}`)" if thread_ts else "")
        + " — ceased work.",
    )
