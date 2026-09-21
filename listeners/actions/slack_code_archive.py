"""Handle the Slack Code "Archive channel" button on a session's summary.

The button (posted by ``listeners/events/slack_code.py`` after the wrap-up
summary) carries ``"<code_channel_id>|<summary_ts>"`` in its value. On click we
call ``agents.conversations.archive`` with ``summary_message_ts`` so Slack
records the summary as the channel's ``summary_message`` property before
archiving (the spec's wrap-up pattern: post summary → offer archive action →
archive on confirm). Best-effort + audited; ``archive_channel`` never raises.
"""
from __future__ import annotations

from logging import Logger

from slack_sdk.web.async_client import AsyncWebClient

from agent import slackcode
from agent.audit import audit_log


async def handle_slack_code_archive(ack, body: dict, client: AsyncWebClient, logger: Logger):
    await ack()
    try:
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        channel_id, _, summary_ts = value.partition("|")
        if not channel_id:
            logger.warning("slackcode_archive: no channel_id in action value %r", value)
            return

        result = await slackcode.archive_channel(
            client, channel_id, summary_message_ts=summary_ts or None
        )
        if result.ok:
            await audit_log(
                client,
                f":package: Claude AI Slack Code channel <#{channel_id}> archived "
                f"(summary ts=`{summary_ts}`).",
            )
        else:
            # already_archived is a benign no-op; anything else is worth a note.
            logger.info("slackcode_archive: archive returned not-ok (%s)", result.error)
            await audit_log(
                client,
                f":warning: Claude AI Slack Code archive of <#{channel_id}> → `{result.error}`.",
            )
    except Exception as e:  # noqa: BLE001 — a button click must never crash the app
        logger.exception("slackcode_archive handler failed")
        await audit_log(
            client,
            f":warning: Claude AI Slack Code archive handler error={type(e).__name__}.",
        )
