"""Handler for `code_channel_action` — a click on an interactive context-bar item.

A context-bar item published with ``item_type: "action"`` renders as a clickable
chip; clicking it delivers a ``code_channel_action`` event to the app (per the
Code channel properties doc). The exact inbound payload shape is beta-confidential,
so this handler is written to (1) LOG the entire raw event verbatim — so a live
click reveals the shape — and (2) best-effort dispatch on whatever key/action id
it can find, reusing the same scripted helpers the slash commands and mention
keywords use. Once the shape is confirmed from a live capture, tighten the
dispatch and drop the raw logging.

Registered in listeners/events/__init__.py and subscribed in manifest.json
bot_events. Gated by SLACK_CODE_ENABLED via the shared config.
"""
from __future__ import annotations

import json
from logging import Logger

from slack_sdk.web.async_client import AsyncWebClient

from agent import slackcode
from listeners.events.slack_code import run_code_action

_CFG = slackcode.load_slack_code_config()

# Context-bar item `key` → scripted action name. The keys we publish on the
# interactive bar map to the same helpers as the slash commands / mention words.
_KEY_TO_ACTION = {
    "create-pr": None,       # handled as a canned message elsewhere; no scripted helper
    "run-tests": None,
    "check": "check",
    "patch": "patch",
    "metrics": "metrics",
    "dashboard": "metrics",
    "recap": "recap",
    "probe-action": None,    # the dev probe chip — no-op beyond logging
}


async def handle_code_channel_action(
    logger: Logger, client: AsyncWebClient = None, event: dict = None,
    body: dict = None, action: dict = None, payload: dict = None, ack=None, **kwargs,
) -> None:
    """Log the raw code_channel_action payload, then best-effort dispatch.

    Delivery shape is beta-confidential and may arrive as an Events-API event OR
    (more likely — it's rejected as a bot event) an interactivity action. Bolt
    injects different kwargs for each (`event` vs `body`/`action`/`ack`), so we
    accept them all and log whichever arrived. The log line is the point: it
    prints the whole payload so we can see channel id, the item key/action id, the
    user, and any response_url/trigger_id. Never raises."""
    # An interactivity action MUST be acked within 3s.
    if ack is not None:
        try:
            await ack()
        except Exception:  # noqa: BLE001
            pass

    # Whichever container Bolt handed us — dump them all so nothing is missed.
    raw = {"event": event, "body": body, "action": action, "payload": payload}
    raw = {k: v for k, v in raw.items() if v is not None}
    logger.warning("code_channel_action RAW PAYLOAD: %s", json.dumps(raw, default=str)[:3000])

    if not _CFG.enabled:
        return

    # Merge the shapes into one dict to hunt for channel id + clicked item key.
    src = {}
    for d in (event, payload, action, body):
        if isinstance(d, dict):
            src = {**d, **src}
    channel_id = (
        src.get("channel")
        or src.get("channel_id")
        or (src.get("item") or {}).get("channel_id")
        or ((src.get("container") or {}).get("channel_id"))
        or ((body or {}).get("channel") or {}).get("id")
    )
    key = (
        src.get("key")
        or src.get("action_id")
        or (src.get("item") or {}).get("key")
        or (src.get("action") or {}).get("key")
        or ((body or {}).get("actions") or [{}])[0].get("action_id")
    )
    logger.warning("code_channel_action parsed: channel_id=%s key=%s", channel_id, key)

    if not channel_id or not key:
        logger.warning("code_channel_action: couldn't parse channel/key — see RAW PAYLOAD above")
        return

    if client is None:
        logger.info("code_channel_action: no client on this delivery shape; logged only")
        return

    scripted = _KEY_TO_ACTION.get(str(key))
    if scripted:
        # check / patch / metrics / recap → the same scripted helpers the slash
        # commands and mention keywords use.
        await run_code_action(client, logger, _CFG, channel_id, scripted)
        return

    # Canned-report chips (mirror the /run-tests and /create-pr slash commands so a
    # bar click == the command). These have no scripted helper, so post inline.
    canned = {
        "run-tests": (":test_tube: Ran the test suite:\n• `unit` — *142 passed*\n"
                      "• `integration` — *38 passed*\n• `lint` — *clean*\n"
                      ":large_green_circle: All green."),
        "create-pr": (f":hierarchy: Opened a draft PR for this branch — {_CFG.fake_pr_url}"),
    }.get(str(key))
    if canned:
        try:
            await client.chat_postMessage(channel=channel_id, markdown_text=canned)
        except Exception:
            logger.exception("code_channel_action: canned post failed for key=%r", key)
        return

    logger.info("code_channel_action: key=%r has no mapped action; logged only", key)
