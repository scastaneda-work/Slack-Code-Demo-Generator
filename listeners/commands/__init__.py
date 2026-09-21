"""Slash-command handlers for the Claude AI bot's Slack Code sessions.

These are the runtime-registered per-channel commands the bot offers inside a
code channel via `agents.conversations.setCommands`. Invocations arrive as normal
slash-command requests (Socket Mode routes them automatically — no request URL
needed).

Two groups:
  - Scenario-artifact commands — `/check-<label>`, `/patch`, `/show-metrics`,
    `/make-recap` — drive the check→patch→re-check loop and the dashboard/recap
    tabs. They delegate to the shared action helpers in
    ``listeners.events.slack_code`` so they behave identically to the in-channel
    mention keywords ("run a check", "generate a patched version", …).
  - Legacy chrome commands — `/create-pr`, `/run-tests`, `/summarize` — canned
    in-voice replies.

All artifacts are FAKE (no real repo/CI) — demo props. Everything only matters
when the Slack Code beta is enabled; the commands simply never get surfaced
otherwise.
"""
from __future__ import annotations

from logging import Logger

from slack_bolt.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_sdk.web.async_client import AsyncWebClient

from agent import slackcode
from listeners.events import slack_code

# Slack Code config, loaded once at import (matches app_mentioned's pattern).
_CFG = slackcode.load_slack_code_config()


async def _post(client: AsyncWebClient, channel_id: str, text: str) -> None:
    await client.chat_postMessage(channel=channel_id, markdown_text=text)


# --- Scenario-artifact commands (the demo's verify / roll-up beats) ----------
async def handle_check(ack: AsyncAck, command: dict, client: AsyncWebClient, logger: Logger):
    await ack()
    channel_id = command.get("channel_id", "")
    await slack_code.run_durability_check(client, logger, _CFG, channel_id)


async def handle_patch(ack: AsyncAck, command: dict, client: AsyncWebClient, logger: Logger):
    await ack()
    channel_id = command.get("channel_id", "")
    await slack_code.run_patch(client, logger, _CFG, channel_id)


async def handle_show_metrics(ack: AsyncAck, command: dict, client: AsyncWebClient, logger: Logger):
    await ack()
    channel_id = command.get("channel_id", "")
    await slack_code.run_show_metrics(client, logger, _CFG, channel_id)


async def handle_make_recap(ack: AsyncAck, command: dict, client: AsyncWebClient, logger: Logger):
    await ack()
    channel_id = command.get("channel_id", "")
    await slack_code.run_make_recap(client, logger, _CFG, channel_id)


# --- Legacy chrome commands --------------------------------------------------
async def handle_create_pr(ack: AsyncAck, command: dict, client: AsyncWebClient, logger: Logger):
    await ack()
    channel_id = command.get("channel_id", "")
    await _post(
        client, channel_id,
        f":github: Opened a draft pull request for this branch: {_CFG.fake_pr_url}\n"
        f"I'll keep the context bar updated as CI runs. Ask me to revise anything before you merge.",
    )


async def handle_run_tests(ack: AsyncAck, command: dict, client: AsyncWebClient, logger: Logger):
    await ack()
    channel_id = command.get("channel_id", "")
    await _post(
        client, channel_id,
        ":test_tube: Ran the test suite:\n"
        "• `unit` — *142 passed*\n"
        "• `integration` — *38 passed*\n"
        "• `lint` — *clean*\n"
        ":large_green_circle: All green.",
    )


async def handle_summarize(ack: AsyncAck, command: dict, client: AsyncWebClient, logger: Logger):
    await ack()
    channel_id = command.get("channel_id", "")
    await _post(
        client, channel_id,
        "*Session summary*\n"
        "• *Task* — the change requested at the top of this channel\n"
        "• *Done* — implemented the change, opened a draft PR, tests green\n"
        "• *Next* — your review; use `/create-pr` to finalize or ask me to revise",
    )


def register(app: AsyncApp):
    """Register the code-channel slash commands. Harmless when Slack Code is off —
    the commands are never surfaced to users unless the session registers them via
    setCommands, but wiring the handlers is zero-cost.

    The check command is scenario-named (/check-<label>, e.g. /check-contrast or
    /check-durability). One running agent serves all seeded stories, so register
    the check command for EVERY known seeded scenario's label — the actual check
    that runs is resolved per-channel (handle_check reads the channel's story). The
    reliable trigger in-channel is still the mention keyword ("run a check"), since
    runtime slash commands only surface once a session registers them via
    setCommands; these boot registrations just wire the handlers."""
    from scenarios import _KNOWN, load_scenario

    seen_labels: set[str] = set()
    for slug in sorted(_KNOWN):
        sc = load_scenario(slug)
        label = getattr(sc, "CHECK_LABEL", "") or ""
        # freeform has no scripted check; skip empty/placeholder labels.
        if not label or label == "build" or label in seen_labels:
            continue
        seen_labels.add(label)
        app.command(f"/check-{label}")(handle_check)
    app.command("/patch")(handle_patch)
    app.command("/show-metrics")(handle_show_metrics)
    app.command("/make-recap")(handle_make_recap)
    app.command("/create-pr")(handle_create_pr)
    app.command("/run-tests")(handle_run_tests)
    app.command("/summarize")(handle_summarize)
