"""Handler for a click on an interactive context-bar item (``item_type: "action"``).

CONFIRMED LIVE (2026-11-02, against a real code channel, superseding the
2026-09-17 note below): clicking an action-type context-bar chip delivers a
Bolt **slash_commands** request — `command` is `/` + the item's `key` (e.g.
`key: "create-pr"` → `command: "/create-pr"`), NOT a `code_channel_action`
events-api event and NOT a `block_actions` interactivity payload. (The beta's
delivery mechanism evidently changed between the two live tests — both
findings were accurate for their own date; this file now follows the current
one.) So the real fix is a slash-command listener, registered below via
`register()`, that catches any `/`-command not already claimed by a literal
`app.command()` elsewhere (listeners/commands/__init__.py) and dispatches on
the command name minus its leading slash — reusing `_KEY_TO_ACTION` as the
shared key → scripted-action mapping.

`handle_code_channel_action` (this module's original name, kept for any
genuine `code_channel_action`/`block_actions` delivery a future beta build
might use) is STILL registered in listeners/events/__init__.py as a catch-all
`app.action()` matcher too, so either delivery path is covered; only the
slash-command path has been confirmed to actually fire as of this date.
Gated by SLACK_CODE_ENABLED via the shared config.
"""
from __future__ import annotations

import json
import re
from logging import Logger

from slack_bolt.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
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

# Slash commands already registered with their own literal handler in
# listeners/commands/__init__.py — the catch-all below must NEVER steal these,
# or Bolt's in-order matching becomes registration-order-dependent and fragile.
# (Checked live: Bolt tries listeners in registration order and the first
# match acks+handles, so registering the catch-all AFTER commands.register()
# in app.py is what actually keeps this safe — this set is a second,
# explicit guard against ever changing that order by accident.)
_ALREADY_REGISTERED = {
    "/patch", "/show-metrics", "/make-recap", "/create-pr", "/run-tests", "/summarize",
}
# Plus every dynamic /check-<label> command — mirror listeners/commands/__init__.py's
# own registration loop so this guard never drifts from what's actually registered.
try:
    from scenarios import _KNOWN as _SCENARIO_SLUGS, load_scenario as _load_scenario
    for _slug in _SCENARIO_SLUGS:
        _label = getattr(_load_scenario(_slug), "CHECK_LABEL", "") or ""
        if _label and _label != "build":
            _ALREADY_REGISTERED.add(f"/check-{_label}")
except Exception:  # noqa: BLE001 — never let a scenario-discovery hiccup block import
    pass

_CATCH_ALL_COMMAND_RE = re.compile(r"^/.+")


async def handle_context_bar_command(
    ack: AsyncAck, command: dict, client: AsyncWebClient, logger: Logger,
) -> None:
    """Catch-all slash-command handler for context-bar action-chip clicks.

    Only runs for a `/`-command NOT already claimed by a literal handler (see
    _ALREADY_REGISTERED) — Bolt's in-order registration means those are tried
    first and win, so this never double-handles them; this guard is a second,
    explicit belt against that ever silently breaking. Logs the raw command
    payload (so an unmapped key's shape is captured, same spirit as the
    original code_channel_action logging), then dispatches via _KEY_TO_ACTION
    the same way handle_code_channel_action does for the other delivery path.
    """
    await ack()
    cmd = (command.get("command") or "").strip()
    logger.warning("context-bar slash command RAW: %s", json.dumps(command, default=str)[:3000])
    if cmd in _ALREADY_REGISTERED:
        # Should be unreachable (registration order means the real handler
        # wins first) — but never silently double-post if it somehow isn't.
        return
    key = cmd.lstrip("/")
    channel_id = command.get("channel_id", "")
    if not channel_id or not key:
        logger.warning("context-bar slash command: missing channel_id/key — see RAW above")
        return
    await _dispatch_key(client, logger, channel_id, key)


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

    await _dispatch_key(client, logger, channel_id, key)


async def _dispatch_key(client: AsyncWebClient, logger: Logger, channel_id: str, key: str) -> None:
    """Shared dispatch for a clicked context-bar item's key, however it arrived
    (slash command or code_channel_action/block_actions). check / patch /
    metrics / recap → the same scripted helpers the slash commands and mention
    keywords use; run-tests / create-pr → a canned post (mirrors their own
    literal slash commands so a bar click behaves identically to typing the
    command); anything unmapped is logged and otherwise ignored."""
    if not _CFG.enabled:
        return
    scripted = _KEY_TO_ACTION.get(str(key))
    if scripted:
        await run_code_action(client, logger, _CFG, channel_id, scripted)
        return

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
            logger.exception("context-bar action: canned post failed for key=%r", key)
        return

    logger.info("context-bar action: key=%r has no mapped action; logged only", key)


def register(app: AsyncApp) -> None:
    """Register the context-bar catch-all slash-command listener.

    MUST be called AFTER listeners/commands/__init__.py's register() in app.py
    so Bolt tries the literal /patch, /create-pr, etc. handlers first and they
    win — this catch-all only ever sees what's left over. Safe either way
    thanks to the _ALREADY_REGISTERED guard inside handle_context_bar_command,
    but correct ordering keeps every command on its own, intended code path.
    """
    try:
        app.command(_CATCH_ALL_COMMAND_RE)(handle_context_bar_command)
    except Exception:  # noqa: BLE001 — tolerate a Bolt version that rejects the matcher
        pass
