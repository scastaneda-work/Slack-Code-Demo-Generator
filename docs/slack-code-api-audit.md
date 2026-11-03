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
| `agents.conversations.setCanvasContent` / `canvases.edit` (comment-anchor preservation) | [`agent/slackcode.py:443`](../agent/slackcode.py) `set_canvas_content()`; called live at [`listeners/events/slack_code.py:940,1035`](../listeners/events/slack_code.py) | **CONFIRMED LIVE 2026-11-02 — comment anchors do NOT survive. This is a real bug, not just a stale comment.** `set_canvas_content()` calls `canvases.edit` with a document-level `replace` (no `section_id`), which replaces the WHOLE canvas document. Live test: created a canvas, added a human comment, re-ran `set_canvas_content()` against the SAME `canvas_id` — Slack then showed the comment anchored to now-deleted content. The function's old docstring claim ("preserving comment anchors") was wrong and has been corrected in the code. `canvases.edit`'s `replace` op CAN target a single `section_id` instead of the whole doc (confirmed via docs.slack.dev/reference/methods/canvases.edit), but that needs `canvases.sections.lookup` first to find the right section — not implemented. **Demo impact: any "living canvas" that re-renders during a session (e.g. the ticking-off-steps Plan canvas) will visibly blow away a customer's or SE's comment on its next refresh.** Flagged in CLAUDE.md for SEs running live demos. |
| `agents.conversations.setCommands` (≤10/channel, `{name, description, argument_hint}`, full-set-replace) | [`agent/slackcode.py:573`](../agent/slackcode.py) `set_commands()`, builtin-collision list line 69, `MAX_COMMANDS = 10` line 68; **wired into the live flow** at [`listeners/events/slack_code.py:1444`](../listeners/events/slack_code.py) | Faithful — field names match the docs exactly (`argument_hint`, not `usage_hint`); confirmed live (`setCommands([probe-cmd])` → `ok=True`, `command_count` echoed back; `setCommands([])` clears it) |
| `agents.conversations.archive` (+ `summary_message_ts`) | [`agent/slackcode.py:587`](../agent/slackcode.py) `archive_channel()` | Faithful |
| `agents.sessions.setStatus` (`active\|processing\|suspended\|closed`) | [`agent/slackcode.py:136`](../agent/slackcode.py) `set_session_status()`, `SESSION_STATUSES` line 46; called throughout the session lifecycle (e.g. [`listeners/events/slack_code.py:674,719,1440`](../listeners/events/slack_code.py)) | Faithful — **confirmed live 2026-11-02**: a full `processing → suspended → active` cycle via `qa.py --probe` all returned `ok=True` against a real code channel. `closed` only exercised with `--archive` (not run this session; separately terminal). |
| `agents.sessions.rename` | [`agent/slackcode.py:148`](../agent/slackcode.py) `rename_session()` | Faithful |
| `record_channel.record_type === "agent_channel"` (code-channel recognition) | [`agent/slackcode.py:657`](../agent/slackcode.py) `is_code_channel()` | Faithful — confirmed live: a channel never created via `agents.conversations.create` correctly returns `False` even post-auth-fix; a channel created via that API correctly returns `True`. |
| `agents.conversations.listViews` envelope key | [`agent/slackcode.py:468`](../agent/slackcode.py) `views_from()` (renamed from private `_views_from`, used by `ensure_view_capacity()` and the probe) | **Bug found and fixed 2026-11-02.** The live response's array lives under `session_views`, not `views` or `agent_session_views` — the old fallback list checked only those two, so it silently returned `[]` against the real API. This broke view-cap enforcement (`ensure_view_capacity`) with no error, just silent non-enforcement. Fixed by adding `session_views` as the first-checked key. |
| `code_channel_action` event (click on an `item_type: "action"` context-bar chip) | [`listeners/events/code_channel_action.py`](../listeners/events/code_channel_action.py) — dispatch handler exists and is registered/subscribed | **CONFIRMED LIVE 2026-11-02 — wrong hook entirely.** Clicking a context-bar `item_type: "action"` chip (key `probe-action`) does NOT fire `code_channel_action`. It dispatches as a Bolt `slash_commands` request with `command` set to `/` + the item's key (here `/probe-action`) — logged as `Unhandled request ({'type': None, 'command': '/probe-action'})` since no `@app.command("/probe-action")` handler exists. **The `code_channel_action.py` listener this repo ships is listening for the wrong event type and will never fire from a context-bar click.** To actually handle these, register `@app.command(re.compile(r".*"))` (or per-key `@app.command()`s) instead of (or in addition to) the current events-api `code_channel_action` subscription. Not yet fixed — flagged as a follow-up, since changing the dispatch mechanism touches the manifest's event subscriptions too. |
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

## Outstanding tasks

All three original items were closed live on 2026-11-02 against a real code channel
(`C0CE5HEA77X`, created via `agents.conversations.create`). Two came back as confirmed-good; one
came back as a confirmed real bug, and a wrong-hook finding surfaced along the way:

1. ~~Confirm `setCanvasContent` comment-anchor behavior live~~ — **done; failed.** Anchors do NOT
   survive (see table above). Fixed: the misleading docstring claim. Not yet fixed: the underlying
   whole-document-replace behavior (would need `canvases.sections.lookup` + a section-scoped
   `replace` — not built).
2. ~~Confirm the real `code_channel_action` payload shape~~ — **done; the listener is wrong.** A
   context-bar action click is a `slash_commands` request, not a `code_channel_action` event (see
   table above). Not yet fixed: `listeners/events/code_channel_action.py` needs a `@app.command()`
   registration, not (or in addition to) its current events-api subscription.
3. ~~Exercise `suspended`/`closed` session statuses at least once~~ — **done; confirmed good.** A
   full `processing → suspended → active` cycle, then `closed` immediately before archiving, via
   `qa.py --probe --archive`, all returned `ok=True`.

New follow-up found during this same live run (not one of the original three):

4. **`listViews`' real envelope key is `session_views`**, not `views`/`agent_session_views` —
   **fixed** in `agent/slackcode.py` (`views_from()`, formerly private `_views_from()`). Confirm
   no other caller assumed the old (wrong) keys when touching this code again.

## Live verification

`qa.py --probe` (see `run_view_probe()` in
[`listeners/events/slack_code.py`](../listeners/events/slack_code.py)) exercises the canvas
two-step flow, `setCanvasContent`, the interactive context-bar item, `setCommands`, and a
`processing → suspended → active` status cycle against a real channel in one run:

```bash
SLACK_BOT_TOKEN=xoxb-… .venv/bin/python qa.py --probe --channel C0CODECHANNEL
```

Run this from your own Terminal (not a nested Claude session — host OAuth vars leak and cause
"not_logged_in") against a real, open code channel the bot is already in — `is_code_channel()`
only returns `True` for a channel actually created via `agents.conversations.create` (confirmed
live: an ordinary pre-existing channel the bot was just invited to returns `False`).

**Canvas re-run note:** the probe now reuses an existing "Probe Canvas" view (by `view_key`) if
one is already attached, instead of always creating a new canvas file on every run — this was a
real bug (see item 4 in Outstanding tasks' sibling finding above the list, and the canvas-anchor
finding in the table) that made a second run incapable of ever testing anchor survival, since it
was always editing a brand-new, comment-free canvas. To test anchor behavior yourself: run once,
add a comment to "Probe Canvas" in Slack, run again on the same channel, and check by eye.
