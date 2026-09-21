"""Slack Code (a.k.a. "Code Channels") Web API wrappers.

Slack Code is the Claude-Tag surface where a coding-task mention spins up a
dedicated channel: the app creates the channel, drives a session status,
paints a context bar (repo/branch/PR/CI) and view tabs (diff/…), then archives
with a summary. The Web API family is `agents.conversations.*` (needs the
`code_channels:manage` scope) and `agents.sessions.*` (needs `chat:write`).

These methods are a confidential beta. As verified against 7018, the feature is
gated server-side: `agents.conversations.*` returns `feature_disabled` until
Slack enables the beta for the app — no scope or manifest key flips it. So every
wrapper here is written to **degrade gracefully**: on `feature_disabled` (or
`missing_scope`) it logs, returns a sentinel, and never raises, so the caller
falls back to a normal reply. When the beta is enabled the same code path starts
working with no changes.

The slack_sdk AsyncWebClient has no typed methods for these beta endpoints, so
we call them via `client.api_call(...)`, which posts to
`https://slack.com/api/<method>` with the bot token already attached.

Everything here is shared across the bot family (symlinked into each bot's
`agent/`), but only a bot whose persona/handler actually creates code channels
will call it. Zero cost when unused.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)

# Errors that mean "the Slack Code beta isn't available to this app" rather than
# "you called it wrong". On any of these we degrade to a normal reply.
FEATURE_GATED_ERRORS = frozenset({"feature_disabled", "missing_scope", "not_allowed_token_type"})

# Valid session statuses (agents.sessions.setStatus). `active` when idle/awaiting
# input, `processing` while working, `suspended` paused, `closed` finished.
# NOTE: `closed` does NOT archive — call archive_channel() for that.
SESSION_STATUSES = frozenset({"active", "processing", "suspended", "closed"})

# Context-bar icon enum (agents.conversations.setProperties). Anything else is
# dropped by Slack, so validate before sending.
CONTEXT_BAR_ICONS = frozenset(
    {"branch", "folder", "hierarchy", "life-ring", "link", "globe", "terminal", "code", "search", "lock"}
)

# View-tab types (agents.conversations.setView).
VIEW_TYPES = frozenset({"diff", "html", "block_kit", "canvas"})

# The channel's view array (agent_session_views) holds AT MOST 5 view tabs across
# all types (html/diff/block_kit/canvas). setView is an upsert on view_key, so
# re-publishing an existing tab is free; only NET-NEW keys count toward the cap.
MAX_VIEWS = 5

# Runtime slash-command rules (agents.conversations.setCommands). Names carry NO
# leading slash, are 1-31 chars, unique within our set, and must not collide with
# a Slack builtin. At most 10 commands may exist across ALL agents in a channel;
# we can only see our own set, so we cap OUR set at 10. Slack enforces the true
# cross-agent limit and returns too_many_commands / colliding_with_builtin /
# duplicate_command — we pre-validate to avoid a whole-set rejection.
MAX_COMMANDS = 10
BUILTIN_COMMANDS = frozenset(
    {"me", "topic", "invite", "leave", "who", "archive", "remind", "dnd",
     "shrug", "status", "away", "active", "mute", "star", "collapse", "expand",
     "rename", "kick", "msg", "dm", "search", "feed", "call", "prefs"}
)


@dataclass
class SlackCodeResult:
    """Outcome of a Slack Code call.

    ok:              the call succeeded.
    feature_gated:   the beta isn't enabled for this app (feature_disabled /
                     missing_scope) — caller should fall back to a normal reply.
    error:           the Slack error code, if any.
    data:            the raw response dict on success (e.g. the new channel_id).
    """

    ok: bool
    feature_gated: bool = False
    error: str | None = None
    data: dict[str, Any] | None = None


async def _call(client: AsyncWebClient, method: str, **params: Any) -> SlackCodeResult:
    """Call a beta Web API method, mapping ANY failure to a sentinel.

    Never raises: a Slack-level error (SlackApiError) OR a transport-level error
    (aiohttp connection/serialization failure — these are NOT SlackApiError and
    used to escape and kill the whole session) both return a SlackCodeResult so
    the caller degrades gracefully. The orchestrator treats a non-ok result as
    "skip this bit of chrome and keep going", never as fatal.
    """
    try:
        # Send as a JSON body, not URL params: these beta methods take nested
        # payloads (e.g. setProperties -> code_channel: {context_bar_items: [...]}),
        # and a nested dict/list in `params=` can't be URL-encoded — aiohttp raises
        # at the transport layer (not a SlackApiError) and kills the call. `json=`
        # is slack_sdk's documented path for structured bodies; the bearer token
        # is attached via header regardless, so the flat calls are unaffected.
        resp = await client.api_call(method, json=params)
    except SlackApiError as e:
        err = e.response.get("error") if e.response is not None else str(e)
        gated = err in FEATURE_GATED_ERRORS
        if gated:
            logger.info("Slack Code method %s gated (%s) — degrading gracefully", method, err)
        else:
            logger.warning("Slack Code method %s failed: %s", method, err)
        return SlackCodeResult(ok=False, feature_gated=gated, error=err)
    except Exception as e:  # noqa: BLE001 — transport/other: degrade, never kill the session
        logger.warning("Slack Code method %s raised %s: %s — degrading gracefully",
                       method, type(e).__name__, e)
        return SlackCodeResult(ok=False, error=f"{type(e).__name__}: {e}")
    # api_call returns an AsyncSlackResponse; .get mirrors a dict.
    if not resp.get("ok", False):
        err = resp.get("error")
        gated = err in FEATURE_GATED_ERRORS
        logger.info("Slack Code method %s not-ok: %s (gated=%s)", method, err, gated)
        return SlackCodeResult(ok=False, feature_gated=gated, error=err)
    return SlackCodeResult(ok=True, data=dict(resp.data) if hasattr(resp, "data") else dict(resp))


# ---------------------------------------------------------------------------
# Session lifecycle (agents.sessions.* — needs only chat:write; works today)
# ---------------------------------------------------------------------------


async def set_session_status(client: AsyncWebClient, channel_id: str, status: str) -> SlackCodeResult:
    """Set the code-channel session status shown in the channel UI.

    `status` in {active, processing, suspended, closed}. In a code channel,
    thread_ts is omitted (the whole channel is one session). Slack shows the
    standard "Working…" loading UX while `processing`.
    """
    if status not in SESSION_STATUSES:
        return SlackCodeResult(ok=False, error=f"invalid status {status!r}")
    return await _call(client, "agents.sessions.setStatus", channel_id=channel_id, status=status)


async def rename_session(client: AsyncWebClient, channel_id: str, title: str) -> SlackCodeResult:
    """Rename the session (updates both the code channel's name and its title)."""
    return await _call(client, "agents.sessions.rename", channel_id=channel_id, title=title)


# Cached bot identity (team_id + bot user_id) from auth.test. Streaming a
# top-level message to a code channel via chat.startStream requires
# recipient_team_id (and recipient_user_id); auth.test is the source of team_id.
# One call per process — the identity never changes for a running bot.
_BOT_IDENTITY: dict[str, str] | None = None


async def get_bot_identity(client: AsyncWebClient) -> dict[str, str]:
    """Return {"team_id", "user_id", "bot_id"} for the bot, cached for the process.

    Backed by auth.test. On any failure returns {} (never raises) so callers can
    treat missing identity as "omit the recipient args" rather than crashing a
    reply. Used to supply chat.startStream's required recipient_team_id in a code
    channel, and `bot_id` to distinguish the bot's OWN posts from other bot-attributed
    events (see is_own_bot_event — an admin/xoxp-token human post carries a DIFFERENT
    bot_id and must NOT be filtered as if it were ours).
    """
    global _BOT_IDENTITY
    if _BOT_IDENTITY is not None:
        return _BOT_IDENTITY
    try:
        resp = await client.auth_test()
        _BOT_IDENTITY = {
            "team_id": resp.get("team_id") or "",
            "user_id": resp.get("user_id") or "",
            "bot_id": resp.get("bot_id") or "",
        }
    except Exception as e:  # noqa: BLE001 — identity is best-effort; degrade to {}
        logger.warning("auth.test failed (%s); streaming recipient args unavailable", type(e).__name__)
        _BOT_IDENTITY = {}
    return _BOT_IDENTITY


async def is_own_bot_event(client: AsyncWebClient, event: dict) -> bool:
    """True if `event` was posted by THIS bot (echo of our own message).

    Correct self-filter for the app_mention / message handlers: skip only our own
    posts, NOT any bot_id. Admin/xoxp-token HUMAN posts arrive with a bot_id set to
    the Slack Admin app's id (a DIFFERENT bot_id) — blanket-dropping any bot_id
    silently discards those real user messages (the documented admin-token-bot_id
    trap). We compare the event's bot_id to our own (from auth.test), and also treat
    a message whose `user` is our bot user id as ours. Best-effort: if identity is
    unavailable, fall back to the old conservative behavior (any bot_id is skipped)
    so we never accidentally answer our own posts."""
    ev_bot_id = event.get("bot_id")
    if not ev_bot_id and not event.get("user"):
        return False
    ident = await get_bot_identity(client)
    own_bot_id = ident.get("bot_id") or ""
    own_user_id = ident.get("user_id") or ""
    if ev_bot_id and own_bot_id:
        return ev_bot_id == own_bot_id
    if event.get("user") and own_user_id:
        return event.get("user") == own_user_id
    # Identity unknown → conservative fallback: treat any bot_id as ours (old behavior).
    return bool(ev_bot_id)


# ---------------------------------------------------------------------------
# Channel lifecycle (agents.conversations.* — needs code_channels:manage + beta)
# ---------------------------------------------------------------------------


async def create_channel(
    client: AsyncWebClient,
    *,
    name: str,
    session_id: str,
    origin_channel_id: str,
    origin_message_ts: str,
) -> SlackCodeResult:
    """Create a code channel off the originating mention.

    `session_id` is an idempotency key: retrying with the same id returns the
    existing channel instead of creating a duplicate (so re-running a demo
    prompt won't spawn N channels). Passing origin_channel_id/ts makes Slack
    match the origin channel's privacy, invite the bot, and record the
    origin_link back-reference. On success, data carries the new channel_id.
    """
    return await _call(
        client,
        "agents.conversations.create",
        name=name,
        session_id=session_id,
        origin_channel_id=origin_channel_id,
        origin_message_ts=origin_message_ts,
    )


async def set_properties(
    client: AsyncWebClient,
    channel_id: str,
    *,
    context_bar_items: list[dict[str, Any]] | None = None,
    summary_message: dict[str, str] | None = None,
    agent_resource: dict[str, Any] | None = None,
) -> SlackCodeResult:
    """Set code-channel properties (agents.conversations.setProperties).

    The single entry point for every writable property block:
      - context_bar_items → code_channel.context_bar_items (max 5; cleaned here;
        the array REPLACES the current set — resend everything you want to keep).
      - summary_message   → code_channel.summary_message ({message_ts, thread_ts?})
        so the current session summary is discoverable WHILE the channel is live,
        not only at archive.
      - agent_resource    → top-level agent_resource ({url, resource_type, title,
        provider}) for a session centered on an external (non-git) resource.
    Only the blocks you pass are sent. `code_channel` fields are merged into one
    object so a single call can set the bar and the summary together.
    """
    params: dict[str, Any] = {"channel_id": channel_id}
    code_channel: dict[str, Any] = {}
    if context_bar_items is not None:
        clean = [it for it in (_clean_context_item(it) for it in context_bar_items[:5]) if it]
        code_channel["context_bar_items"] = clean
    if summary_message is not None:
        code_channel["summary_message"] = _clean_summary_message(summary_message)
    if code_channel:
        params["code_channel"] = code_channel
    if agent_resource is not None:
        params["agent_resource"] = _clean_agent_resource(agent_resource)
    if len(params) == 1:  # nothing to set
        return SlackCodeResult(ok=False, error="set_properties called with no property blocks")
    return await _call(client, "agents.conversations.setProperties", **params)


async def set_context_bar(client: AsyncWebClient, channel_id: str, items: list[dict[str, Any]]) -> SlackCodeResult:
    """Set the context bar (repo/branch/PR/CI …). Thin wrapper over set_properties.
    The array REPLACES the current set — resend everything you want to keep. Max 5
    items; invalid icons are dropped before sending.
    """
    return await set_properties(client, channel_id, context_bar_items=items)


def _clean_summary_message(sm: dict[str, Any]) -> dict[str, str]:
    """Keep only message_ts (required) + thread_ts (optional) for summary_message."""
    out: dict[str, str] = {}
    if sm.get("message_ts"):
        out["message_ts"] = str(sm["message_ts"])
    if sm.get("thread_ts"):
        out["thread_ts"] = str(sm["thread_ts"])
    return out


def _clean_agent_resource(res: dict[str, Any]) -> dict[str, str]:
    """Keep only the documented agent_resource fields, within their length limits:
    url (2048), resource_type (64), title (255), provider (64)."""
    out: dict[str, str] = {}
    for field_, cap in (("url", 2048), ("resource_type", 64), ("title", 255), ("provider", 64)):
        val = res.get(field_)
        if val:
            out[field_] = str(val)[:cap]
    return out


def _clean_context_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Keep only valid context-bar fields; drop the item if it lacks key/label."""
    key = item.get("key")
    label = item.get("label")
    if not key or not label:
        return None
    out: dict[str, Any] = {"key": str(key)[:64], "label": str(label)[:128]}
    icon = item.get("icon")
    if icon in CONTEXT_BAR_ICONS:
        out["icon"] = icon
    if item.get("url"):
        out["url"] = str(item["url"])[:2048]
    if item.get("item_type") in ("info", "action"):
        out["item_type"] = item["item_type"]
    return out


async def set_view(
    client: AsyncWebClient,
    channel_id: str,
    *,
    view_type: str,
    view_key: str | None = None,
    name: str | None = None,
    content: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
    base_branch: str | None = None,
    head_branch: str | None = None,
    canvas_id: str | None = None,
    access_level: str | None = None,
) -> SlackCodeResult:
    """Create/update a view tab (diff/html/block_kit/canvas). Upsert by view_key
    (diff views are keyed implicitly, so view_key is optional there).

    `name` is the view tab's display label (the `name` field of the
    agent_session_views entry). Non-diff tabs (html/block_kit/canvas) should
    always pass one so the tab shows a readable label rather than a blank/derived
    one; diff tabs can omit it (Slack labels the code tab itself).

    Per-type payload (CONFIRMED live via the in-channel probe, since the beta's
    per-method reference is confidential):
      - diff:      content (unified-diff string) + base_branch/head_branch
      - html:      content (a self-contained HTML string) + name
      - block_kit: `blocks` — a real JSON ARRAY, NOT a json.dumps'd string + name
      - canvas:    `canvas_id` of an ALREADY-CREATED canvas + name. A canvas view
                   does NOT take inline content — you first create the canvas via
                   canvases.create (or create_canvas() below), then attach it here
                   by id. (Every inline-content variant — document_content object,
                   flat string, json string — returns missing_required_arg because
                   the missing arg is canvas_id.) Optional access_level="comment"
                   keeps the agent sole author while users comment.
    """
    if view_type not in VIEW_TYPES:
        return SlackCodeResult(ok=False, error=f"invalid view_type {view_type!r}")
    params: dict[str, Any] = {"channel_id": channel_id, "type": view_type}
    if view_key:
        params["view_key"] = view_key
    if name:
        params["name"] = name
    if content is not None:
        params["content"] = content
    if blocks is not None:
        params["blocks"] = blocks  # block_kit views take a real array, not a string
    if canvas_id is not None:
        params["canvas_id"] = canvas_id  # canvas views attach an existing canvas by id
    if access_level is not None:
        params["access_level"] = access_level
    if base_branch is not None:
        params["base_branch"] = base_branch
    if head_branch is not None:
        params["head_branch"] = head_branch
    return await _call(client, "agents.conversations.setView", **params)


async def create_canvas(client: AsyncWebClient, *, title: str, markdown: str) -> SlackCodeResult:
    """Create a standalone canvas (canvases.create) and return its id in data.

    Step 1 of the canvas-view flow: canvases.create takes a `title` + the
    structured document_content object {"type":"markdown","markdown":<text>} and
    returns a canvas_id, which set_view(view_type="canvas", canvas_id=…) then
    attaches as a tab. Needs the canvases:write scope. On success, data carries
    canvas_id."""
    return await _call(
        client, "canvases.create",
        title=title,
        document_content={"type": "markdown", "markdown": markdown},
    )


async def publish_canvas_view(
    client: AsyncWebClient, channel_id: str, *, title: str, markdown: str,
    view_key: str, name: str, access_level: str | None = None,
) -> SlackCodeResult:
    """Create a canvas and attach it as a code-channel view tab, in one call.

    The confirmed two-step flow: canvases.create → set_view(type=canvas,
    canvas_id). Returns the set_view result (its data carries canvas_id/view_id/
    file_id) on success, or the failing step's result. Use this the FIRST time you
    publish a canvas tab; to EDIT it in place afterwards (preserving comment
    anchors) use set_canvas_content with the returned canvas_id instead of
    re-creating."""
    created = await create_canvas(client, title=title, markdown=markdown)
    if not created.ok:
        return created
    cid = (created.data or {}).get("canvas_id")
    if not cid:
        return SlackCodeResult(ok=False, error="canvases.create ok but no canvas_id", data=created.data)
    return await set_view(
        client, channel_id, view_type="canvas", canvas_id=cid,
        view_key=view_key, name=name, access_level=access_level,
    )


async def get_canvas(client: AsyncWebClient, channel_id: str, *, view_key: str | None = None,
                     canvas_id: str | None = None) -> SlackCodeResult:
    """Read a canvas view's content and its comment threads
    (agents.conversations.getCanvas). Identify the canvas by view_key or canvas_id.
    Lets the agent answer questions about a plan doc the user has commented on.

    NOTE: this beta method's contract is UNCONFIRMED by our live probe — degrades
    gracefully (returns a non-ok sentinel) if the shape is wrong, so a caller must
    treat a failure as "comments unavailable", not fatal. Don't build a hard
    dependency on it until a probe confirms the response shape."""
    params: dict[str, Any] = {"channel_id": channel_id}
    if view_key:
        params["view_key"] = view_key
    if canvas_id:
        params["canvas_id"] = canvas_id
    return await _call(client, "agents.conversations.getCanvas", **params)


async def set_canvas_content(client: AsyncWebClient, *, canvas_id: str, markdown: str) -> SlackCodeResult:
    """Replace a canvas's whole content in place, preserving comment anchors, via
    canvases.edit with a `replace` operation (public method, needs canvases:write —
    which the canvas-view flow already requires). This is the "living plan doc"
    edit path: the attached view tab re-renders to the new content and keeps its
    view_id/comments, so it's preferred over re-creating + re-attaching a canvas
    on every follow-up. Identify the canvas by the canvas_id returned from
    create_canvas / publish_canvas_view.

    (The beta also exposes agents.conversations.setCanvasContent, but its exact
    contract is unconfirmed; canvases.edit is the confirmed, documented path.)"""
    return await _call(
        client, "canvases.edit",
        canvas_id=canvas_id,
        changes=[{"operation": "replace",
                  "document_content": {"type": "markdown", "markdown": markdown}}],
    )


async def list_views(client: AsyncWebClient, channel_id: str) -> SlackCodeResult:
    """List the views attached to a code channel (agents.conversations.listViews).
    Returns the canonical agent_session_views array (data["views"] on success)."""
    return await _call(client, "agents.conversations.listViews", channel_id=channel_id)


def _views_from(result: SlackCodeResult) -> list[dict[str, Any]]:
    """Pull the view array out of a listViews result, tolerant of key naming.

    The managed property is `agent_session_views`; the listViews method is
    documented to return the same entries. We accept a few plausible envelope
    keys (`views` / `agent_session_views` / `data`) so a shape tweak in the beta
    doesn't silently break cap enforcement. Returns [] on a non-ok result.
    """
    if not result.ok or not result.data:
        return []
    for key in ("views", "agent_session_views", "data"):
        v = result.data.get(key)
        if isinstance(v, list):
            return [it for it in v if isinstance(it, dict)]
    return []


async def remove_view(client: AsyncWebClient, channel_id: str, *, view_id: str | None = None,
                      view_key: str | None = None) -> SlackCodeResult:
    """Remove a view tab (agents.conversations.removeView), by view_id or view_key.

    Used to prune a stale tab — e.g. before publishing a NET-NEW view when the
    channel already holds MAX_VIEWS. Pass whichever identifier you have; view_id
    is the encoded tab id from listViews, view_key is your stable agent-assigned
    key. At least one is required.
    """
    if not view_id and not view_key:
        return SlackCodeResult(ok=False, error="remove_view needs view_id or view_key")
    params: dict[str, Any] = {"channel_id": channel_id}
    if view_id:
        params["view_id"] = view_id
    if view_key:
        params["view_key"] = view_key
    return await _call(client, "agents.conversations.removeView", **params)


async def ensure_view_capacity(client: AsyncWebClient, channel_id: str, *, view_key: str,
                               logger_: logging.Logger | None = None) -> SlackCodeResult:
    """Make room for a NET-NEW view tab keyed `view_key`, honoring the MAX_VIEWS cap.

    setView is an upsert: re-publishing an existing view_key is free and never
    trips the cap, so this is a no-op then. Only when `view_key` is NOT already
    present AND the channel is at MAX_VIEWS does it prune the oldest keyed,
    non-diff tab (diff tabs are the code tab — never auto-pruned) to avoid Slack
    silently dropping a tab. Best-effort: a listViews failure returns ok (we don't
    block a publish on being unable to read the array — Slack still upserts).
    """
    lv = await list_views(client, channel_id)
    if not lv.ok:
        # Can't read the array (feature gated, or listViews unsupported): don't
        # block the publish — setView will upsert regardless.
        return SlackCodeResult(ok=True, data={"skipped": "listViews unavailable"})
    views = _views_from(lv)
    keys = {v.get("view_key") for v in views if v.get("view_key")}
    if view_key in keys or len(views) < MAX_VIEWS:
        return SlackCodeResult(ok=True, data={"pruned": None})
    # At cap and adding a new key: prune the oldest keyed non-diff tab.
    prunable = [v for v in views if v.get("view_key") and v.get("type") != "diff"]
    prunable.sort(key=lambda v: v.get("date_added") or 0)
    if not prunable:
        if logger_:
            logger_.warning("view cap reached on %s and nothing prunable; setView may drop a tab", channel_id)
        return SlackCodeResult(ok=False, error="view_cap_reached_nothing_prunable")
    victim = prunable[0]
    if logger_:
        logger_.info("view cap: pruning stale tab %r to add %r on %s",
                     victim.get("view_key"), view_key, channel_id)
    return await remove_view(client, channel_id,
                             view_id=victim.get("view_id"), view_key=victim.get("view_key"))


def _clean_commands(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize + validate a runtime slash-command set before sending.

    Per the Agent slash commands spec: strip a leading slash from `name`, enforce
    1-31 chars, drop duplicates (first wins), drop builtin collisions, and cap the
    set at MAX_COMMANDS. Keeps only the documented fields (name/description/
    argument_hint, plus should_escape if set). This turns a would-be whole-set
    rejection (too_many_commands / colliding_with_builtin / duplicate_command)
    into a best-effort set that Slack accepts.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cmd in commands:
        if not isinstance(cmd, dict):
            continue
        name = str(cmd.get("name", "")).strip().lstrip("/")
        if not (1 <= len(name) <= 31):
            continue
        if name in BUILTIN_COMMANDS or name in seen:
            continue
        seen.add(name)
        entry: dict[str, Any] = {"name": name}
        if cmd.get("description"):
            entry["description"] = str(cmd["description"])
        if cmd.get("argument_hint"):
            entry["argument_hint"] = str(cmd["argument_hint"])
        if cmd.get("should_escape") is True:
            entry["should_escape"] = True
        out.append(entry)
        if len(out) >= MAX_COMMANDS:
            break
    return out


async def set_commands(client: AsyncWebClient, channel_id: str, commands: list[dict[str, Any]]) -> SlackCodeResult:
    """Register the channel's runtime slash commands (e.g. /create-pr, /run-tests,
    /summarize). Each call REPLACES the set — always send the complete list.

    The set is normalized + validated first (see _clean_commands): leading slash
    stripped, 1-31 chars, unique, no builtin collisions, capped at MAX_COMMANDS.
    Send an empty list to clear the agent's commands. Requires code_channels:manage
    and a slash_command_url in the manifest (or Socket Mode / a hosted app, which
    are routed automatically) — else Slack returns no_actions_url.
    """
    clean = _clean_commands(commands)
    return await _call(client, "agents.conversations.setCommands", channel_id=channel_id, commands=clean)


async def archive_channel(client: AsyncWebClient, channel_id: str, summary_message_ts: str | None = None) -> SlackCodeResult:
    """Archive the code channel. Pass summary_message_ts (the ts of the agent's
    closing summary message) so Slack records it as the channel's
    summary_message and it stays discoverable after archival."""
    params: dict[str, Any] = {"channel_id": channel_id}
    if summary_message_ts:
        params["summary_message_ts"] = summary_message_ts
    return await _call(client, "agents.conversations.archive", **params)


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# is_code_channel result cache (avoid a conversations.info per message)
# ---------------------------------------------------------------------------
# is_code_channel is called on the HOT path: handle_message runs it for every
# top-level non-mention message in every channel the bot is in, to decide whether
# to treat the message as a code-channel follow-up. One conversations.info
# round-trip per message, workspace-wide, is wasteful and re-couples the hot path
# to the same (Grid-fragile) call is_code_channel makes. So we cache the verdict
# per channel_id, in-process:
#   • True  is cached PERMANENTLY — record_channel.record_type is written at
#     creation and never changes, so a channel that IS a code channel stays one.
#   • False is cached with a SHORT TTL — a channel could BECOME a code channel
#     (the bot is added to one; or a just-created channel whose record_type
#     hasn't propagated yet races a False). A short TTL re-checks soon so a new
#     code channel isn't stuck misclassified, while still collapsing the flood of
#     repeat calls in ordinary (non-code) channels.
# In-process only (a restart re-learns), matching the other registries here.
import time as _time

_CODE_CHANNEL_CACHE: dict[str, tuple[bool, float]] = {}
_CODE_CHANNEL_NEG_TTL_S = 60.0  # how long a False verdict is trusted before re-checking


def mark_code_channel(channel_id: str) -> None:
    """Seed the cache: `channel_id` IS a code channel (True, permanent). Called at
    creation so we never spend a conversations.info confirming a channel we just
    made. Safe to call repeatedly."""
    if channel_id:
        _CODE_CHANNEL_CACHE[channel_id] = (True, 0.0)  # 0.0 sentinel = permanent (never expires)


def _cached_code_channel(channel_id: str) -> bool | None:
    """Return the cached verdict for `channel_id`, or None if there's no live entry.
    True never expires; False expires after _CODE_CHANNEL_NEG_TTL_S."""
    hit = _CODE_CHANNEL_CACHE.get(channel_id)
    if hit is None:
        return None
    verdict, expires = hit
    if verdict:
        return True  # permanent
    if _time.monotonic() < expires:
        return False
    _CODE_CHANNEL_CACHE.pop(channel_id, None)  # stale negative — force a re-check
    return None


def clear_code_channel_cache(channel_id: str | None = None) -> None:
    """Drop a channel's cached verdict (or the whole cache when channel_id is None).
    Mainly for tests / a forced re-probe."""
    if channel_id is None:
        _CODE_CHANNEL_CACHE.clear()
    else:
        _CODE_CHANNEL_CACHE.pop(channel_id, None)


async def is_code_channel(client: AsyncWebClient, channel_id: str, *, use_cache: bool = True) -> bool:
    """True if `channel_id` is a Slack Code channel.

    The reliable signal is properties.record_channel.record_type ==
    "agent_channel" (set at creation, present on every code channel). Do NOT
    gate on code_channel content — it can be {} on a fresh channel. Never raises.

    Cached per channel_id (see the cache block above): a True verdict is reused
    permanently and a False verdict for a short TTL, so the hot follow-up path
    doesn't spend a conversations.info on every message. Pass use_cache=False to
    force a live check (e.g. the dev view-probe, which must reflect current state).

    On an Enterprise Grid org, `conversations.info` for a channel that lives in a
    specific workspace can require a `team_id`; without it the call can fail and
    we'd wrongly report False — which would drop EVERY in-channel follow-up out of
    the code-channel path. So we pass the bot's own team_id (cached auth.test) when
    we have it. It's harmless on a non-Grid workspace and omitted if identity
    lookup returns nothing, so nothing regresses there. NOTE: confirm live on the
    target org — this is the fix if follow-up detection misbehaves.
    """
    if use_cache:
        cached = _cached_code_channel(channel_id)
        if cached is not None:
            return cached
    kwargs: dict[str, Any] = {"channel": channel_id, "include_num_members": False}
    ident = await get_bot_identity(client)
    if ident.get("team_id"):
        kwargs["team_id"] = ident["team_id"]
    try:
        resp = await client.conversations_info(**kwargs)
    except SlackApiError as e:
        logger.info("is_code_channel: conversations.info failed (%s)", e.response.get("error") if e.response else e)
        # Don't cache an API-error False: it's likely transient (rate limit, a
        # missing team_id on Grid) and caching it would suppress the retry that
        # a fresh call on the next message would make.
        return False
    props = (resp.get("channel") or {}).get("properties") or {}
    record = props.get("record_channel") or {}
    verdict = record.get("record_type") == "agent_channel"
    # Cache the verdict: True permanent, False on a short TTL (see cache block).
    _CODE_CHANNEL_CACHE[channel_id] = (True, 0.0) if verdict else (False, _time.monotonic() + _CODE_CHANNEL_NEG_TTL_S)
    return verdict


# ---------------------------------------------------------------------------
# Human participants: scripted chime-ins + roster invite (humans + AI together)
# ---------------------------------------------------------------------------
# A code channel's value is humans AND the agent working together. We show 1–2
# people from the origin thread participating alongside Claude, two ways that
# compose:
#   • spoof their VOICE — post a scripted line with chat:write.customize
#     (username=<display name>), which shows their name (bot avatar + APP tag).
#     Token-free, portable across demo orgs (names are stable), the shareable
#     default.
#   • add their PRESENCE — resolve their real user by name in the live org and
#     conversations.invite them, so they also show in the roster. Best-effort;
#     silently skipped when the name doesn't resolve (other orgs still get the
#     spoofed voice).
# These are plain Web API calls (not beta agents.* methods), so they call the
# client directly rather than _call(); every one is best-effort and never raises.


async def post_as_participant(
    client: AsyncWebClient, channel_id: str, *, name: str, text: str, icon_url: str | None = None,
    thread_ts: str | None = None,
) -> SlackCodeResult:
    """Post a scripted human chime-in in a code channel, spoofing the persona via
    chat:write.customize. Passes BOTH username=name AND icon_url (the persona's real
    avatar) — icon_url is required to override the bot's photo, and passing it per
    message also makes Slack render each chime-in's identity distinctly instead of
    coalescing successive customized posts onto one name. Falls back to icon_emoji
    when no avatar url is known. Never raises; a missing_scope (customize not
    granted) or any error degrades to a non-ok sentinel so the caller keeps going.

    ``thread_ts`` posts the chime-in as a THREAD reply (used by the in-thread Claude
    Tag surface); omit it for a top-level code-channel post (the original behavior)."""
    if not name or not text:
        return SlackCodeResult(ok=False, error="post_as_participant: name/text required")
    kwargs: dict[str, Any] = {"channel": channel_id, "text": text, "username": name}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    if icon_url:
        kwargs["icon_url"] = icon_url
    else:
        kwargs["icon_emoji"] = ":bust_in_silhouette:"  # a neutral human icon beats the bot avatar
    try:
        resp = await client.chat_postMessage(**kwargs)
    except SlackApiError as e:
        err = e.response.get("error") if e.response is not None else str(e)
        logger.warning("post_as_participant(%s) failed: %s", name, err)
        return SlackCodeResult(ok=False, error=err)
    except Exception as e:  # noqa: BLE001 — transport/other: degrade, never kill the beat
        logger.warning("post_as_participant(%s) raised %s: %s", name, type(e).__name__, e)
        return SlackCodeResult(ok=False, error=f"{type(e).__name__}: {e}")
    return SlackCodeResult(ok=bool(resp.get("ok", True)), data=dict(resp.data) if hasattr(resp, "data") else None)


async def react(client: AsyncWebClient, channel_id: str, ts: str, emoji: str) -> SlackCodeResult:
    """Add an emoji reaction (as the bot) to a message. Best-effort; a duplicate
    (already_reacted) or any error is swallowed. Used sparingly so the channel
    feels alive without over-reacting."""
    if not ts or not emoji:
        return SlackCodeResult(ok=False, error="react: ts/emoji required")
    try:
        await client.reactions_add(channel=channel_id, timestamp=ts, name=emoji)
    except SlackApiError as e:
        err = e.response.get("error") if e.response is not None else str(e)
        if err not in ("already_reacted",):
            logger.info("react :%s: failed: %s", emoji, err)
        return SlackCodeResult(ok=False, error=err)
    except Exception as e:  # noqa: BLE001
        logger.info("react :%s: raised %s", emoji, type(e).__name__)
        return SlackCodeResult(ok=False, error=f"{type(e).__name__}: {e}")
    return SlackCodeResult(ok=True)


# Demo-persona email resolution for the participant invite. Every demo org's users
# share ONE deterministic address format — demoeng+<stem>_<orgnum>@slack-corp.com —
# where only <orgnum> varies per org (e.g. adam_ferris → demoeng+adam_ferris_7018@…).
# So a participant carries a stable `email_stem` ("adam_ferris"), the bot derives
# the org number from its own team url, constructs the exact address, and resolves
# it via users.lookupByEmail. This is EXACT and unambiguous — unlike name matching,
# where "Adam" is one user's real_name AND another's display name. Portable: only
# the org number changes, and it's derived, not hardcoded.
#
# Org number: parsed from auth.test's `url` (e.g. https://slack-demo-7018.enterprise
# .slack.com/ → "7018"), overridable via SLACK_DEMO_ORG_NUM for an org whose domain
# doesn't carry the number. Cached with the bot identity.
_DEMO_ORG_NUM: str | None = None

# Workspace team_id (a `T...`) for team-scoped calls on an Enterprise Grid org.
# users.list / users.lookupByEmail REQUIRE a workspace team_id there — without it
# they fail with `missing_argument` (verified live on 7018). auth.test on Grid
# returns the ENTERPRISE id (`E...`), which is the wrong value; the workspace id
# arrives on every inbound event, so we capture it from the Bolt context at the
# handler entry points. Falls back to conversations.info's shared_team_ids /
# context_team_id, then to auth.test's team_id when it's already a `T...`.
_WORKSPACE_TEAM_ID: str | None = None


def set_workspace_team_id(team_id: str | None) -> None:
    """Record the workspace team_id (a `T...`) seen on an inbound event's context.
    Cheap + idempotent; the first real `T...` wins."""
    global _WORKSPACE_TEAM_ID
    if team_id and team_id.startswith("T") and not _WORKSPACE_TEAM_ID:
        _WORKSPACE_TEAM_ID = team_id
        logger.info("workspace team_id captured: %s", team_id)


async def get_workspace_team_id(client: AsyncWebClient, channel_id: str | None = None) -> str | None:
    """Return the workspace team_id for team-scoped calls, or None (caller then
    omits team_id — correct on a non-Grid org). Order: the id captured from an
    event context (set_workspace_team_id); else conversations.info on channel_id
    (shared_team_ids[0] / context_team_id); else auth.test's team_id IF it's a
    `T...` (non-Grid). SLACK_WORKSPACE_TEAM_ID overrides everything."""
    global _WORKSPACE_TEAM_ID
    env = (os.environ.get("SLACK_WORKSPACE_TEAM_ID") or "").strip()
    if env:
        _WORKSPACE_TEAM_ID = env
        return env
    if _WORKSPACE_TEAM_ID:
        return _WORKSPACE_TEAM_ID
    # Try the channel's own team from conversations.info.
    if channel_id:
        try:
            resp = await client.conversations_info(channel=channel_id)
            ch = resp.get("channel") or {}
            shared = ch.get("shared_team_ids") or []
            cand = (shared[0] if shared else None) or ch.get("context_team_id")
            if cand and str(cand).startswith("T"):
                _WORKSPACE_TEAM_ID = str(cand)
                return _WORKSPACE_TEAM_ID
        except Exception:  # noqa: BLE001 — best-effort
            pass
    # Fall back to auth.test team_id only if it's a workspace id (non-Grid org).
    ident = await get_bot_identity(client)
    tid = ident.get("team_id") or ""
    if tid.startswith("T"):
        _WORKSPACE_TEAM_ID = tid
        return tid
    return None


async def _demo_org_num(client: AsyncWebClient) -> str | None:
    """Derive the demo-org number for building persona emails. Prefers
    SLACK_DEMO_ORG_NUM; else parses the trailing number out of auth.test's team url
    (…-<num>.…). Cached per process. Returns None if it can't be determined (the
    invite tier then simply skips — spoofed voices still post)."""
    global _DEMO_ORG_NUM
    if _DEMO_ORG_NUM is not None:
        return _DEMO_ORG_NUM or None
    env = (os.environ.get("SLACK_DEMO_ORG_NUM") or "").strip()
    if env:
        logger.info("participant org-num from SLACK_DEMO_ORG_NUM=%s", env)
        _DEMO_ORG_NUM = env
        return env
    try:
        resp = await client.auth_test()
        url = resp.get("url") or ""
        m = re.search(r"-(\d+)\.", url)  # slack-demo-7018.enterprise.slack.com → 7018
        _DEMO_ORG_NUM = m.group(1) if m else ""
        logger.info("participant org-num derived from auth.test url=%r → %r", url, _DEMO_ORG_NUM)
    except Exception as e:  # noqa: BLE001 — best-effort; skip the invite tier if unknown
        logger.warning("demo org-num derive failed (%s) — participant invite will skip", type(e).__name__)
        _DEMO_ORG_NUM = ""
    return _DEMO_ORG_NUM or None


def _persona_email(stem: str, org_num: str) -> str:
    """Construct a demo persona's email from its stable stem + the org number."""
    return f"demoeng+{stem}_{org_num}@slack-corp.com"


# Per-process cache of resolved participants: email_stem → {user_id, icon_url,
# real_name} (or None when unresolvable). Feeds BOTH the roster invite and the
# avatar on the spoofed chime-in, so we resolve each persona at most once.
_PARTICIPANT_CACHE: dict[str, dict[str, str] | None] = {}
# Last resolve error code seen (e.g. "missing_scope", "users_not_found"), surfaced
# in the invite status so a live run reveals WHY resolution failed without log-diving.
_LAST_RESOLVE_ERROR: str | None = None
# Compact per-resolve trace (team_id used, lookup outcome, list member/email counts)
# surfaced in the invite status — turns a "0/N" audit line into a self-explaining one
# so we don't have to read the bot's (unreadable) DEBUG firehose.
_LAST_RESOLVE_TRACE: str | None = None


def _info_from_user(user: dict) -> dict[str, str] | None:
    """Extract {user_id, icon_url, real_name} from a users.* user object."""
    profile = user.get("profile") or {}
    info: dict[str, str] = {}
    if user.get("id"):
        info["user_id"] = user["id"]
    icon = profile.get("image_192") or profile.get("image_72") or profile.get("image_48")
    if icon:
        info["icon_url"] = icon
    rn = profile.get("real_name") or user.get("real_name")
    if rn:
        info["real_name"] = rn
    return info or None


async def _resolve_via_users_list(client: AsyncWebClient, email: str, team_id: str | None) -> dict[str, str] | None:
    """Fallback resolver: scan users.list (needs only users:read) for a member whose
    profile.email matches. On Enterprise Grid users.list REQUIRES a workspace
    team_id (else `missing_argument`), so we pass it when known. Paginated +
    bounded; returns None on no match or if email isn't exposed in the payload."""
    global _LAST_RESOLVE_TRACE
    target = email.strip().lower()
    seen = 0
    with_email = 0
    try:
        cursor: str | None = None
        pages = 0
        while True:
            kwargs: dict[str, Any] = {"limit": 200}
            if team_id:
                kwargs["team_id"] = team_id
            if cursor:
                kwargs["cursor"] = cursor
            resp = await client.users_list(**kwargs)
            if not resp.get("ok", True):
                _LAST_RESOLVE_TRACE = f"list ok=false error={resp.get('error')}"
                return None
            for m in resp.get("members", []) or []:
                seen += 1
                if m.get("deleted") or m.get("is_bot"):
                    continue
                pemail = ((m.get("profile") or {}).get("email") or "").strip().lower()
                if pemail:
                    with_email += 1
                if pemail and pemail == target:
                    return _info_from_user(m)
            cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
            pages += 1
            if not cursor or pages >= 25:
                break
        _LAST_RESOLVE_TRACE = f"list seen={seen} with_email={with_email} no-match"
    except SlackApiError as e:  # noqa: BLE001 — fallback is best-effort
        _LAST_RESOLVE_TRACE = f"list SlackApiError={e.response.get('error') if e.response is not None else e}"
        logger.warning("participant resolve via users.list → %s", _LAST_RESOLVE_TRACE)
    except Exception as e:  # noqa: BLE001
        _LAST_RESOLVE_TRACE = f"list raised {type(e).__name__}"
        logger.warning("participant resolve via users.list raised %s", type(e).__name__)
    return None


async def resolve_participant(client: AsyncWebClient, email_stem: str) -> dict[str, str] | None:
    """Resolve a persona stem to {user_id, icon_url, real_name}. Tries
    users.lookupByEmail first (needs users:read.email); on ANY failure (ok:false,
    error, missing scope) falls back to scanning users.list by profile.email (needs
    only users:read) — which is what survives an Enterprise Grid install where
    lookupByEmail misbehaves. Cached per process. Returns None if the org number is
    unknown or neither path resolves; the caller then skips the invite and posts the
    chime-in with a fallback icon. Logs loudly so a live run shows what happened."""
    global _LAST_RESOLVE_ERROR
    stem = (email_stem or "").strip()
    if not stem:
        return None
    if stem in _PARTICIPANT_CACHE:
        return _PARTICIPANT_CACHE[stem]
    org = await _demo_org_num(client)
    if not org:
        _PARTICIPANT_CACHE[stem] = None
        return None
    email = _persona_email(stem, org)
    # Workspace team_id for team-scoped calls (required on Grid, omitted elsewhere).
    team_id = await get_workspace_team_id(client)
    result: dict[str, str] | None = None
    lookup_err: str | None = None
    # --- Primary: users.lookupByEmail (with team_id on Grid) ---
    try:
        kw: dict[str, Any] = {"email": email}
        if team_id:
            kw["team_id"] = team_id
        resp = await client.users_lookupByEmail(**kw)
        if not resp.get("ok", True):
            lookup_err = resp.get("error") or "not_ok"
        else:
            result = _info_from_user(resp.get("user") or {})
    except SlackApiError as e:
        lookup_err = e.response.get("error") if e.response is not None else str(e)
    except Exception as e:  # noqa: BLE001
        lookup_err = type(e).__name__
    # --- Fallback: users.list scan by email (Grid-safe, users:read only) ---
    if result is None:
        global _LAST_RESOLVE_TRACE
        logger.warning("participant lookupByEmail %s failed (%s, team_id=%s) — trying users.list",
                       email, lookup_err, team_id)
        result = await _resolve_via_users_list(client, email, team_id)
        if result is None:
            # Build a self-explaining trace for the audit line (team_id used + why both missed).
            _LAST_RESOLVE_TRACE = f"team_id={team_id or 'none'} lookup={lookup_err or 'no-user'}; {_LAST_RESOLVE_TRACE or 'list none'}"
            if lookup_err:
                _LAST_RESOLVE_ERROR = lookup_err  # surface the primary error if both miss
    logger.info("participant resolve %s → %s", email,
                {k: (v[:40] if k == "icon_url" else v) for k, v in (result or {}).items()} or "MISS")
    _PARTICIPANT_CACHE[stem] = result
    return result


async def invite_participants(
    client: AsyncWebClient, channel_id: str, email_stems: list[str],
) -> str:
    """Fidelity tier (best-effort): resolve each persona stem (via resolve_participant)
    and conversations.invite them so they show in the channel roster. EXACT
    resolution (no name ambiguity), portable across demo orgs (only the derived org
    number changes). Returns a short human-readable status string (for the caller to
    audit) — e.g. "invited 2/2" or "org-num unknown". Any failure is swallowed; the
    spoofed chime-ins still carry the story. No-ops on an empty list."""
    stems = [s for s in (email_stems or []) if s and s.strip()]
    if not stems:
        return "no participants"
    org = await _demo_org_num(client)
    if not org:
        logger.warning("participant invite SKIPPED for %s — org-num unknown", channel_id)
        return "org-num unknown"
    uids: list[str] = []
    misses: list[str] = []
    for stem in stems:
        info = await resolve_participant(client, stem)
        if info and info.get("user_id"):
            uids.append(info["user_id"])
        else:
            misses.append(stem)
    if not uids:
        logger.warning("participant invite: 0 of %d stems resolved for %s (misses=%s, trace=%s)",
                       len(stems), channel_id, misses, _LAST_RESOLVE_TRACE)
        return f"0/{len(stems)} resolved" + (f" [{_LAST_RESOLVE_TRACE}]" if _LAST_RESOLVE_TRACE else "")
    invited = 0
    try:
        await client.conversations_invite(channel=channel_id, users=",".join(uids))
        invited = len(uids)
        logger.info("participant invite: added %d user(s) to %s", invited, channel_id)
    except SlackApiError as e:
        err = e.response.get("error") if e.response is not None else str(e)
        logger.warning("participant invite batch → %s; retrying one-by-one", err)
        # already_in_channel (batch) is fine; otherwise try one-by-one so one bad id
        # doesn't drop the rest. Count already_in_channel as present.
        for uid in uids:
            try:
                await client.conversations_invite(channel=channel_id, users=uid)
                invited += 1
            except SlackApiError as e2:
                err2 = e2.response.get("error") if e2.response is not None else str(e2)
                if err2 == "already_in_channel":
                    invited += 1
                elif err2 != "cant_invite_self":
                    logger.warning("participant invite %s → %s", uid, err2)
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001 — invite is best-effort; never break the session
        logger.warning("participant invite raised %s — spoofed voices still cover it", type(e).__name__)
        return f"invite error {type(e).__name__}"
    return f"invited {invited}/{len(stems)}" + (f" (unresolved: {','.join(misses)})" if misses else "")


# ---------------------------------------------------------------------------
# Config + intent gate (demo wiring; not part of the Web API)
# ---------------------------------------------------------------------------


@dataclass
class SlackCodeConfig:
    """Gate + scripted-demo values, sourced from env so retargeting the demo
    needs no code edit. `enabled` (SLACK_CODE_ENABLED) gates the whole feature
    for this bot; everything else is the fake, scripted repo/branch/PR the demo
    shows (the bot has no real repo)."""

    enabled: bool = False
    # Claude Tag (the lightweight in-thread checklist surface) sub-gate. Defaults to
    # follow `enabled` (so one switch turns the whole "Claude in Slack" demo on); set
    # SLACK_TAG_ENABLED=0 to run Slack Code without Tag, or =1 to force it. Consulted
    # by the app_mention Tag-routing gate and the message.py thread-follow gate.
    tag_enabled: bool = False
    # Which demo scenario's scripted artifacts to show. Resolved to a module by
    # claude-ai-bot's scenarios.load_scenario. Left EMPTY here on purpose: an empty
    # value means "use the scenarios package's own _DEFAULT" (load_scenario("") and
    # load_scenario(None) both fall back to _DEFAULT), so there is ONE source of
    # truth for the default scenario — this shared config does not hardcode a
    # second, drift-prone default. Override per launch via SLACK_CODE_SCENARIO.
    scenario: str = ""
    intent_keywords: list[str] = field(default_factory=lambda: [
        # Ops / refactor tasks (the scripted-scenario triggers)
        "migrate", "refactor", "fix the", "open a pr", "open a pull request",
        "failing test", "flaky test", "deploy", "implement", "write a script",
        # Build-a-thing tasks (the freeform live-artifact triggers). Kept phrase-
        # specific ("create a", not bare "create") so a normal @-mention question
        # rarely trips them; the gate only fires on an explicit mention anyway and
        # falls back to a normal reply when it doesn't match. Shared default, but
        # only a bot with SLACK_CODE_ENABLED consults it (Claude AI today).
        "create a", "create an", "build a", "build an", "build me", "make me a",
        "make a ", "make an", "generate a", "generate an", "write a ", "write an",
        "code up", "put together a", "whip up",
        # Canned-story triggers (so a natural phrasing that ROUTES to a seeded
        # story also OPENS a channel — keep in step with scenarios.route_scenario).
        "redesign", "homepage", "landing page", "webhook",
        # checkout_incident openers
        "roll back", "rollback", "retry storm", "incident", "payment-service",
        # flaky_test openers ("flaky test" is above; add bare forms for "test is flaky")
        "flaky", "flake", "login_redirect",
        # sql_migration openers
        "migration", "add a column", "add a last_login", "last_login_at", "backfill",
    ])
    channel_name_prefix: str = "claude-"
    fake_repo: str = "acme/billing-service"
    fake_repo_url: str = "https://github.com/acme/billing-service"
    fake_branch_prefix: str = "feat/"
    fake_pr_url: str = "https://github.com/acme/billing-service/pull/482"


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


def load_slack_code_config() -> SlackCodeConfig:
    """Build config from env. SLACK_CODE_ENABLED gates everything; the other
    SLACK_CODE_* vars override the scripted demo values."""
    cfg = SlackCodeConfig(enabled=_truthy(os.environ.get("SLACK_CODE_ENABLED")))
    # Tag sub-gate: defaults to follow `enabled`; SLACK_TAG_ENABLED overrides either way.
    _tag_env = os.environ.get("SLACK_TAG_ENABLED")
    cfg.tag_enabled = _truthy(_tag_env) if _tag_env is not None else cfg.enabled
    scenario = os.environ.get("SLACK_CODE_SCENARIO")
    if scenario and scenario.strip():
        cfg.scenario = scenario.strip()
    kw = os.environ.get("SLACK_CODE_INTENT_KEYWORDS")
    if kw:
        cfg.intent_keywords = [k.strip().lower() for k in kw.split(",") if k.strip()]
    for attr, envvar in [
        ("channel_name_prefix", "SLACK_CODE_CHANNEL_PREFIX"),
        ("fake_repo", "SLACK_CODE_FAKE_REPO"),
        ("fake_repo_url", "SLACK_CODE_FAKE_REPO_URL"),
        ("fake_branch_prefix", "SLACK_CODE_FAKE_BRANCH_PREFIX"),
        ("fake_pr_url", "SLACK_CODE_FAKE_PR_URL"),
    ]:
        val = os.environ.get(envvar)
        if val:
            setattr(cfg, attr, val)
    return cfg


def is_coding_task(text: str, cfg: SlackCodeConfig) -> bool:
    """Deterministic keyword gate — does this mention warrant a code channel?

    Deliberately simple + case-insensitive so a scripted demo prompt triggers a
    channel every time (more repeatable than an LLM classifier for a demo).
    Returns False when Slack Code is disabled, so callers gate on this one call.
    """
    if not cfg.enabled:
        return False
    low = f" {text.lower()} "
    return any(kw in low for kw in cfg.intent_keywords)


def channel_name_for(task_text: str, cfg: SlackCodeConfig) -> str:
    """Slugify a task mention into a Slack channel name (lowercase, hyphenated,
    prefixed, <=80 chars)."""
    slug = re.sub(r"[^a-z0-9]+", "-", task_text.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:60] or "session"
    return f"{cfg.channel_name_prefix}{slug}"[:80].rstrip("-")


# ---------------------------------------------------------------------------
# Cooperative stop registry (agent_session_stopped)
# ---------------------------------------------------------------------------
# When Slack fires `agent_session_stopped` for a code channel, the session must
# cease work immediately and post nothing further. We can't kill an in-flight
# claude-agent-sdk call mid-token, but the orchestrator checks this registry
# between steps and bails, and any buffered reply is dropped. Keyed by channel_id.
_STOPPED_CHANNELS: set[str] = set()


def mark_session_stopped(channel_id: str) -> None:
    """Record that a code channel's session was stopped (from the
    agent_session_stopped handler)."""
    if channel_id:
        _STOPPED_CHANNELS.add(channel_id)


def is_session_stopped(channel_id: str) -> bool:
    """True if the session for this channel has been stopped; the orchestrator
    checks this between steps and aborts."""
    return channel_id in _STOPPED_CHANNELS


def clear_session_stopped(channel_id: str) -> None:
    """Reset the stop flag (e.g. when a fresh session starts in the channel)."""
    _STOPPED_CHANNELS.discard(channel_id)


# ---------------------------------------------------------------------------
# Replay-dedup registry (Slack event redelivery)
# ---------------------------------------------------------------------------
# Slack redelivers an app_mention on `retry_reason: timeout` when the handler
# blocks past the ack window (a full Slack Code session runs synchronously and
# easily exceeds it). Each redelivery has the SAME message ts, so we key an
# in-process guard on the session/idempotency key and only let the first one
# through — otherwise one mention spawns N duplicate code channels. In-process
# only (a bot restart resets it), matching the SessionStore/stop-registry model;
# fine for a demo. This is app-side because the beta does not appear to honor
# the `session_id` idempotency key we pass to agents.conversations.create.
_STARTED_SESSIONS: set[str] = set()


def mark_session_started(key: str) -> bool:
    """Claim a session key. Returns True if this is the first time we've seen it
    (proceed), False if it's already in flight/handled (a redelivery — skip).
    Atomic within the single-threaded asyncio loop: check-and-add, no await."""
    if not key or key in _STARTED_SESSIONS:
        return False
    _STARTED_SESSIONS.add(key)
    return True


def clear_session_started(key: str) -> None:
    """Release a session key (e.g. on a clean failure that should be retryable)."""
    _STARTED_SESSIONS.discard(key)


# ---------------------------------------------------------------------------
# Durable per-channel state (survives a bot restart)
# ---------------------------------------------------------------------------
# The per-channel registries below (scenario lock, patched flag, live artifact,
# recap canvas id) are keyed by channel_id and must OUTLIVE a bot restart: a code
# channel opened by one process and continued after a relaunch (or the next day)
# has to stay on its own story, keep its patched state, and keep editing its own
# artifact/canvas. Otherwise a restart silently drops the channel→scenario lock
# and a follow-up check runs the DEFAULT scenario (the wrong-story bug).
#
# So these four are backed by a small JSON file, loaded once at import and
# rewritten on every mutation. The file lives at SLACK_CODE_STATE_PATH, or
# ./.slack_code_state.json in the process CWD (run.sh cd's into the bot's own
# dir, so it's per-bot and won't collide). Writes are best-effort + atomic
# (temp-file rename); a write failure never breaks a reply. The stop/started
# registries are deliberately NOT persisted — a restart SHOULD clear in-flight
# state (nothing is mid-turn across a restart).
import json as _json

_STATE_PATH = os.environ.get("SLACK_CODE_STATE_PATH") or os.path.join(os.getcwd(), ".slack_code_state.json")


def _load_state() -> dict[str, Any]:
    """Load the durable state file. Returns {} on any problem (missing/corrupt) so
    a fresh or unreadable file just starts empty — never raises."""
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — missing/corrupt/unreadable → start empty
        return {}


def _save_state() -> None:
    """Persist the durable registries atomically (temp file + rename). Best-effort:
    a write failure is logged and swallowed so it can never break a Slack reply."""
    try:
        payload = {
            "patched": sorted(_PATCHED_CHANNELS),
            "artifact": _CHANNEL_ARTIFACT,
            "scenario": _CHANNEL_SCENARIO,
            "recap_canvas": _CHANNEL_RECAP_CANVAS,
        }
        tmp = f"{_STATE_PATH}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(payload, f)
        os.replace(tmp, _STATE_PATH)
    except Exception as e:  # noqa: BLE001 — persistence is best-effort
        logger.warning("slack_code state save failed (%s) — continuing in-memory", type(e).__name__)


_STATE = _load_state()


# ---------------------------------------------------------------------------
# Patched-channel registry (the check → patch → re-check demo loop)
# ---------------------------------------------------------------------------
# The scripted check fails on first run and passes after a patch. We track which
# code channels have been patched so the re-check returns PASS. DURABLE (see
# above): survives a restart so a re-check after a relaunch still reports PASS.
_PATCHED_CHANNELS: set[str] = set(_STATE.get("patched", []))


def mark_channel_patched(channel_id: str) -> None:
    """Record that this code channel's change has been patched (check now passes)."""
    if channel_id:
        _PATCHED_CHANNELS.add(channel_id)
        _save_state()


def is_channel_patched(channel_id: str) -> bool:
    """True if the patch has been applied — the re-check should report PASS."""
    return channel_id in _PATCHED_CHANNELS


def clear_channel_patched(channel_id: str) -> None:
    """Reset the patched flag (e.g. when a fresh session starts in the channel)."""
    _PATCHED_CHANNELS.discard(channel_id)
    _save_state()


# ---------------------------------------------------------------------------
# Per-channel live artifact registry (dynamic, editable Code/Preview artifacts)
# ---------------------------------------------------------------------------
# A Code channel's Code (diff) + Preview (html) tabs are a LIVING artifact: the
# agent generates a self-contained file on the first turn and edits it on every
# follow-up ("make the color scheme red"). To render the Code tab as an
# *incremental* diff on follow-ups, and to keep the Preview in sync, we remember
# the current file (name + full html) per code channel. DURABLE (see above): a
# follow-up edit after a restart still diffs against the stored file. Keyed by
# channel_id.
_CHANNEL_ARTIFACT: dict[str, dict[str, str]] = dict(_STATE.get("artifact", {}))


def set_channel_artifact(channel_id: str, *, filename: str, html: str) -> None:
    """Store (or replace) the current live artifact for a code channel."""
    if channel_id:
        _CHANNEL_ARTIFACT[channel_id] = {"filename": filename, "html": html}
        _save_state()


def get_channel_artifact(channel_id: str) -> dict[str, str] | None:
    """Return the current live artifact ({"filename","html"}) for a code channel,
    or None if this channel has no artifact yet (first turn)."""
    return _CHANNEL_ARTIFACT.get(channel_id)


def clear_channel_artifact(channel_id: str) -> None:
    """Drop the stored artifact (e.g. when a fresh session starts in the channel)."""
    _CHANNEL_ARTIFACT.pop(channel_id, None)
    _save_state()


# ---------------------------------------------------------------------------
# Per-channel scenario registry (one agent, keyword-routed stories)
# ---------------------------------------------------------------------------
# One running agent offers all demo stories. The story is chosen from the
# @-mention text when the code channel is CREATED (see scenarios.route_scenario)
# and recorded here, so every follow-up in that channel — mention, non-mention,
# or slash command — continues the SAME story regardless of the SLACK_CODE_SCENARIO
# env default. DURABLE (see above): survives a restart, so a channel opened by one
# process stays locked to its story after a relaunch (fixes the wrong-story bug
# where a restart dropped the lock and a follow-up ran the default scenario).
# Keyed by channel_id.
_CHANNEL_SCENARIO: dict[str, str] = dict(_STATE.get("scenario", {}))


def set_channel_scenario(channel_id: str, slug: str) -> None:
    """Record which demo story this code channel is running."""
    if channel_id and slug:
        _CHANNEL_SCENARIO[channel_id] = slug
        _save_state()


def get_channel_scenario(channel_id: str) -> str | None:
    """Return the story slug this code channel was opened with, or None if unknown
    (e.g. a channel created before this feature) — the caller falls back to the
    env default then."""
    return _CHANNEL_SCENARIO.get(channel_id)


def clear_channel_scenario(channel_id: str) -> None:
    """Drop the stored scenario (e.g. when a fresh session starts in the channel)."""
    _CHANNEL_SCENARIO.pop(channel_id, None)
    _save_state()


# ---------------------------------------------------------------------------
# Per-channel recap-canvas registry (the living leadership-recap canvas tab)
# ---------------------------------------------------------------------------
# The recap can be published as a canvas VIEW TAB (create canvas → attach by id).
# We remember the created canvas_id per channel so a follow-up ("update the
# recap") EDITS the same canvas in place (canvases.edit) — preserving its view
# tab + any comments — instead of creating a second canvas. DURABLE (see above):
# survives a restart so a later "update the recap" edits the same canvas. Keyed
# by channel_id.
_CHANNEL_RECAP_CANVAS: dict[str, str] = dict(_STATE.get("recap_canvas", {}))


def set_recap_canvas_id(channel_id: str, canvas_id: str) -> None:
    """Record the recap canvas id for a channel (after the first publish)."""
    if channel_id and canvas_id:
        _CHANNEL_RECAP_CANVAS[channel_id] = canvas_id
        _save_state()


def get_recap_canvas_id(channel_id: str) -> str | None:
    """Return the channel's recap canvas id, or None if not yet published."""
    return _CHANNEL_RECAP_CANVAS.get(channel_id)


def clear_recap_canvas_id(channel_id: str) -> None:
    """Drop the stored recap canvas id (fresh session)."""
    _CHANNEL_RECAP_CANVAS.pop(channel_id, None)
    _save_state()


# ---------------------------------------------------------------------------
# Context-echo recognition (self-trigger guard, spec-aligned)
# ---------------------------------------------------------------------------
# When Slack Code creates a code channel it auto-posts a "Context" backlink
# message into it — a bare permalink to the origin message. Because that origin
# message @-mentions the bot, Slack re-fires an `app_mention` for the backlink,
# INSIDE the new channel, attributed to the human author (bot_id=None). Left
# unhandled, the bot tries to "answer" the permalink (and, on older builds,
# could even re-trigger channel creation).
#
# The reliable, zero-API, zero-race tell: the mention's text is JUST a link (an
# `<url|label>` / `<url>` span) once the `<@bot>` mention is stripped — there is
# no actual instruction left. A real in-channel mention always leaves prose. So
# we recognize the echo from the text alone, with no dependency on the freshly
# created channel's `record_type` propagating (which races at creation time) and
# no need to track channel IDs we created (that ID doesn't even exist yet when
# the echo can arrive). This replaces the old created-channel registry, which
# could not structurally close that race.
def is_context_echo(event: dict) -> bool:
    """True if `event` is Slack's auto-posted 'Context' backlink, not a real ask.

    When Slack Code creates a code channel it posts a "Context" backlink into the
    new channel that quotes the origin message. Because that quoted origin
    @-mentions the bot, Slack re-fires it as BOTH a `message` and an `app_mention`
    event (attributed to the human, bot_id=None). Left unhandled, the bot answers
    the backlink — and worse, the quoted "> migrate the billing cron…" line trips
    the coding-task gate into spawning a NESTED code channel (the cascade).

    We recognize it purely from the event PAYLOAD, which carries markers a human
    message never has — no API call (so no `channels:read` dependency), no timing
    race:
      • an `attachments[].agent_channel_unfurl` block (the code-channel unfurl), or
      • a `blocks[]` entry whose `block_id` starts with `agent_channel_origin_`
        (`agent_channel_origin_context` / `agent_channel_origin_status_…`).
    Deliberately does NOT infer from text: a bare "@Claude" with no ask is a real
    (if empty) mention that app_mentioned answers with a prompt, not an echo.
    Never raises; on any odd shape it returns False (safer to answer than to drop).
    """
    try:
        for att in event.get("attachments") or []:
            if isinstance(att, dict) and att.get("agent_channel_unfurl"):
                return True
        for blk in event.get("blocks") or []:
            bid = blk.get("block_id", "") if isinstance(blk, dict) else ""
            if bid.startswith("agent_channel_origin_"):
                return True
    except Exception:  # noqa: BLE001 — recognition must never break the handler
        return False
    return False
