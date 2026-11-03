# Slack Code API audit — sim vs. the live docs

Checks the simulator's `agent/slackcode.py` / `listeners/events/slack_code.py` against the
official Slack Code docs (fetched directly from docs.slack.dev — not inferred), now that the
beta is enabled for this app. Everything below is **verification + tightening**, not a
build-missing-features list: the sim already implements nearly the full documented surface.

Sources:
- https://docs.slack.dev/ai/slack-code.md
- https://docs.slack.dev/ai/slack-code/code-channel-properties.md
- https://docs.slack.dev/ai/slack-code/agent-slash-commands.md

## Legend

- **Faithful** — matches the docs exactly, confirmed by reading the code.
- **Stale** — implemented, but a comment/constant disagrees with the current docs.
- **Library-only** — the wrapper exists but nothing in the demo flow calls it yet.
- **Unconfirmed (beta)** — the code itself flags the exact shape as unverified against a live
  response; needs the live-probe run (see "Live verification" below) to close.

| Documented affordance | Sim implementation | Status |
|---|---|---|
| `agents.conversations.create` | [`agent/slackcode.py:216`](../agent/slackcode.py) `create_channel()` | Faithful |
| `agents.conversations.setProperties` → context bar (max 5, icon enum, `item_type` info\|action) | [`agent/slackcode.py:246`](../agent/slackcode.py) `set_properties()`, icon enum at line 50 (`CONTEXT_BAR_ICONS`), 5-cap enforced at line 266 | Faithful — icon enum matches docs exactly (`branch, folder, hierarchy, life-ring, link, globe, terminal, code, search, lock`) |
| `agents.conversations.setProperties` → `agent_resource` (external resource) | [`agent/slackcode.py:301`](../agent/slackcode.py) `_clean_agent_resource()`, wired via `set_properties(agent_resource=...)` | Faithful |
| `agents.conversations.setProperties` → `code_channel.summary_message` | [`agent/slackcode.py:291`](../agent/slackcode.py) `_clean_summary_message()` | Faithful |
| `agents.conversations.setView` (`diff\|html\|block_kit\|canvas`, max 5 tabs, upsert by `view_key`) | [`agent/slackcode.py:329`](../agent/slackcode.py) `set_view()`, `VIEW_TYPES` line 55, `MAX_VIEWS = 5` line 60, cap enforcement at [`agent/slackcode.py:504`](../agent/slackcode.py) `ensure_view_capacity()` | Faithful |
| `agents.conversations.listViews` | [`agent/slackcode.py:462`](../agent/slackcode.py) `list_views()` | Faithful |
| `agents.conversations.removeView` | [`agent/slackcode.py:485`](../agent/slackcode.py) `remove_view()` | Faithful |
| `agents.conversations.getCanvas` | [`agent/slackcode.py:425`](../agent/slackcode.py) `get_canvas()` | Faithful |
| `agents.conversations.setCanvasContent` (comment-anchor preservation) | [`agent/slackcode.py:443`](../agent/slackcode.py) `set_canvas_content()`; called live at [`listeners/events/slack_code.py:940,1035`](../listeners/events/slack_code.py) | **Unconfirmed (beta)** — the docs page we fetched (code-channel-properties.md) does not describe comment-anchor behavior at all; the code's own comment (line 448 in the pre-merge version) flagged "the beta also exposes setCanvasContent, but its exact [comment-anchor] behavior" as unverified. Needs a live round-trip: add a comment to the canvas, call `setCanvasContent`, confirm the comment survives. |
| `agents.conversations.setCommands` (≤10/channel, `{name, description, argument_hint}`, full-set-replace) | [`agent/slackcode.py:573`](../agent/slackcode.py) `set_commands()`, builtin-collision list line 69, `MAX_COMMANDS = 10` line 68; **wired into the live flow** at [`listeners/events/slack_code.py:1444`](../listeners/events/slack_code.py) | Faithful — field names match the docs exactly (`argument_hint`, not `usage_hint`) |
| `agents.conversations.archive` (+ `summary_message_ts`) | [`agent/slackcode.py:587`](../agent/slackcode.py) `archive_channel()` | Faithful |
| `agents.sessions.setStatus` (`active\|processing\|suspended\|closed`) | [`agent/slackcode.py:136`](../agent/slackcode.py) `set_session_status()`, `SESSION_STATUSES` line 46; called throughout the session lifecycle (e.g. [`listeners/events/slack_code.py:674,719,1440`](../listeners/events/slack_code.py)) | Faithful — only `active`/`processing` are exercised live today; `suspended`/`closed` are declared but never called from the demo flow (see Task below) |
| `agents.sessions.rename` | [`agent/slackcode.py:148`](../agent/slackcode.py) `rename_session()` | Faithful |
| `record_channel.record_type === "agent_channel"` (code-channel recognition) | [`agent/slackcode.py:657`](../agent/slackcode.py) `is_code_channel()` | Faithful |
| `code_channel_action` event (click on an `item_type: "action"` context-bar chip) | [`listeners/events/code_channel_action.py`](../listeners/events/code_channel_action.py) — dispatch handler exists and is registered/subscribed | **Unconfirmed (beta)** — the handler's own docstring says "the exact inbound payload shape is beta-confidential," logs the raw event verbatim, and best-effort-dispatches on whatever key it can find. Needs one live click to confirm, then the dispatch can be tightened and the raw-logging removed. |
| `chat.startStream` / `appendStream` / `stopStream` (live-text streaming) | Used throughout `listeners/events/slack_code.py` for the "thinking" pulse and streamed replies | Faithful — this is a stable, non-beta Web API surface; no doc-gap here |
| Manifest `features.code_channels.enabled` + `slash_command_url` | [`manifest.json`](../manifest.json) — `enabled: true` declared; `slash_command_url` **not yet set** (the app runs Socket Mode, which the docs note as an alternative delivery path for runtime commands) | Faithful for this app's transport; `slash_command_url` is only needed for HTTP-mode apps |
| Scope `code_channels:manage` | [`manifest.json`](../manifest.json) bot scopes | Faithful |
| Event `message.session` (code-channel + session-thread messages) | [`manifest.json`](../manifest.json) bot_events | Faithful |
| Event `agent_session_stopped` | [`manifest.json`](../manifest.json) bot_events; handled in [`listeners/events/agent_session_stopped.py`](../listeners/events/agent_session_stopped.py) | Faithful |

## Debunked claims (do not act on these — an early research pass asserted them, the docs do not confirm them)

- An `agent_view` manifest *feature block* distinct from `assistant_view` — not in the Slack
  Code docs we fetched. (The manifest does separately declare `assistant_view` for the
  general-assistant surface, which is correct and unrelated.)
- A `task_display_mode` streaming parameter — not on any fetched page.
- Events `app_context_changed`, `agent_session_title_changed` — not on any fetched page.
- Extra `agents.sessions.setStatus` values beyond `active/processing/suspended/closed` — the
  fetched pages name exactly these four, no more.

## Outstanding tasks (tracked, not yet done)

1. **Confirm `setCanvasContent` comment-anchor behavior live** (see table above).
2. **Confirm the real `code_channel_action` payload shape** by clicking a live action chip and
   reading the bot's stderr (the handler already logs it verbatim for this purpose).
3. **Exercise `suspended`/`closed` session statuses at least once** — declared and valid, but
   nothing in the current demo flow calls them, so there's no live confirmation they render as
   documented (the docs only describe `processing`'s "Working…" UX in detail).

See [scripts: live verification](#live-verification) below for the tool that runs 1–3.

## Live verification

`qa.py --probe` already existed before this audit and proves the canvas two-step flow
(`canvases.create` → `setView(type=canvas, canvas_id=…)`) and the interactive context-bar item
against a real channel — see `run_view_probe()` in
[`listeners/events/slack_code.py`](../listeners/events/slack_code.py). This audit extends that
same probe (rather than adding a parallel script) to also exercise `setCommands`, a full
`processing → active → suspended → closed` status cycle, and `archive` with a summary message —
closing outstanding tasks 1–3 above in one run. See the updated docstring on `run_view_probe` for
the exact invocation:

```bash
SLACK_BOT_TOKEN=xoxb-… .venv/bin/python qa.py --probe --channel C0CODECHANNEL
```

Run this from your own Terminal (not a nested Claude session — host OAuth vars leak and cause
"not_logged_in") against a real, open code channel the bot is already in.
