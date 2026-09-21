# CLAUDE.md — Claude AI bot (the demo)

This bot impersonates **Claude Tag** — Anthropic's official `@Claude` app in
Slack — for live demos in the 7018 org (Slack app `A0B4Z2RSKRB`, launched via
`../run.sh claude`).

For the shared kit/SDK/runtime mechanics (Bolt, Agent SDK, Bedrock CLI,
shared vs per-bot files, the taskplan/blockkit render path), see the parent
doc `../.claude/CLAUDE.md`. This file covers only what's specific to *this*
demo.

## What the demo shows

Claude AI presents as Claude Tag: a helpful, direct, thoughtful assistant that
works inside the team's Slack. Two surfaces:

- **DMs / assistant panel** — conversational plain text (no Block Kit). Catch-up
  and summarization is the hero: summarize a thread/channel around *decisions
  made, open questions, and who each item is waiting on*.
- **Channels (@-mention)** — when asked for an incident summary / synthesis /
  action items, it replies as a Block Kit card with an `actions` block; quick
  questions stay plain text.

> **@-mention vs. ambient listening — the key channel distinction (verified live 2026-09-20).**
> In a **regular channel**, Claude only receives messages that **explicitly @-mention it**
> — plain thread replies are NOT delivered to the bot, even in a thread it's already
> engaged in. So any follow-up that should steer Claude (e.g. a correction on a Claude
> Tag checklist) MUST @-mention it, or Claude never sees it. In a **code channel** (the
> Slack Code beta surface) Claude is a dedicated session participant and **ambiently
> listens** — non-mention messages ARE delivered and handled as follow-ups
> (`_reply_in_code_channel_followup`). Two consequences for this bot: (1) a Claude Tag
> thread correction has to tag `@Claude`; (2) the self-filter must skip only the bot's
> OWN posts, not any `bot_id` — an admin/xoxp-token human post carries a different
> `bot_id` and must still be delivered (`slackcode.is_own_bot_event` — the
> admin-token-`bot_id` trap: never blanket-drop `bot_id`).

The **taskplan `plan` mode** ("Thinking…") is the signature flourish — a live
checklist that streams in and finalizes on the same message, mirroring how the
real Claude Tag edits its checklist in place. It leads multi-step replies.

## Canned demo scenario — Checkout API Latency Incident (P1)

The persona (`personas/claude_ai.md`) bakes in a scripted incident so the demo
lands without live data:

- **Elliott Ward** (VP Eng) raises a P1: checkout API latency ~10× spike, orders
  failing at payment.
- **Cindy Chen** (Staff Eng) correlates it in Datadog to a 2:14 PM deploy;
  connection pool saturated, p99 200ms → 3.2s, retry storms.
- **Lauren Bailey** (EM) confirms `payment-service v2.4.1` at 2:14 PM — too-
  aggressive Stripe retry logic.
- In incident mode Claude **acts** (files the rollback PR, creates the ticket)
  and presents what it did, with `actions` buttons — it doesn't hand the team a
  to-do list.

The 5 vetted demo questions live in `examples/demo_questions.md` (read directly
by `qa.py --vet-questions`).

## App-specific config / branding

- `manifest.json`: `background_color` = Claude coral `#D97757`; `assistant_view.
  suggested_prompts` = the Claude-Tag-style starters. Bot handle is
  `@claude_ai_local` until manually renamed at api.slack.com (re-do after any
  reinstall).
- Persona rules that matter here: Slack mrkdwn single-asterisk bold; first
  output char must be a backtick when emitting a fence; `taskplan` then
  `blockkit` order; attach a source when asserting a fact but never fabricate a
  numbered-citation footnote system; be honest that it only sees the channel/
  thread it's tagged in.

## Slack Code extension (gated, beta) — with the Artifacts golden-standard build

Claude Tag's "Slack Code" surface — a coding-task mention spins up a dedicated
channel with a session status, a context bar (repo/branch/PR/CI/check), a set of
**Artifact view tabs**, runtime slash commands, and an archive-with-summary
lifecycle. Modeled on the Slack Code Channels demo video's "3 questions, 0
meetings" arc: did it ship right (plan + diff + preview) → is it working
(dashboard) → is leadership behind it (recap).

**Status: `SLACK_CODE_ENABLED` gates it; the beta is LIVE on this app
(`A0B4Z2RSKRB`, verified 2026-09-09).** With the beta on, the Artifact tabs
actually render; the code still degrades gracefully to a normal thread reply if
`agents.conversations.*` ever returns `feature_disabled`. All artifacts are FAKE
(no real repo/GitHub) — convincing demo props.

**ONE AGENT, ALL STORIES (keyword-routed, locked per channel).** A single running
bot serves EVERY scripted story plus freeform — no `SLACK_CODE_SCENARIO` relaunch
to switch. The opening @-mention picks the story via
`scenarios.route_scenario(task_text)` (source of truth: `_ROUTE_KEYWORDS` +
`_KNOWN` in `scenarios/__init__.py`):
- "homepage / redesign / landing page / hero / welloguard" → `website_redesign` (default)
- "billing webhook / stripe webhook / durable queue / webhook refactor" → `billing_webhook`
- "checkout latency / payment-service / retry storm / rollback / p99 / incident" → `checkout_incident`
- "flaky / flake / login_redirect / rerun the test" → `flaky_test`
- "migration / add a column / last_login_at / backfill / alter table" → `sql_migration`
- any other build task → `freeform`

The chosen slug is recorded on the channel
(`slackcode.set_channel_scenario`) at creation, and every follow-up (mention,
non-mention, slash command) resolves it via `slack_code._scenario_for(channel_id,
cfg)` = `get_channel_scenario(channel_id) or cfg.scenario` — so each channel is
locked to its story; a new story = a new mention = a new channel. `is_coding_task`
was widened so the story triggers (incl. "redesign"/"homepage"/"webhook") open a
channel; keep its keywords in step with `route_scenario`. `SLACK_CODE_SCENARIO`
still works as the fallback default for a channel with no recorded story (pre-build
or post-restart), but the demo no longer depends on it. Launch is just
`SLACK_CODE_ENABLED=1 AUDIT_CHANNEL_ID=C0AUC5B30SE ./run.sh claude` — omit the
scenario env. Scripted `/check`/`/patch`/`/metrics`/`/recap` no-op gracefully in a
freeform channel (guarded by `is_seeded`, via `_no_scripted_action`).

**LIVE-EDITABLE ARTIFACTS (the "breathing" Code/Preview tabs) — the baseline for
every code channel.** The Code (diff) + Preview (html) tabs are a living thing:
the agent GENERATES a complete self-contained HTML file and edits it on every
turn, and both tabs re-render to match. This is not a mode — it's how a code
channel behaves. Two flavors:
- **`freeform`** (`SLACK_CODE_SCENARIO=freeform`, `SEED = False`) — no scripted
  content. `@Claude create a simple html snake game` → the agent builds
  `snake-game.html` from scratch (full-add diff `+N -0` on `master`, Preview
  renders it); "make the color scheme red" → the agent regenerates the full file,
  we diff new-vs-previous (incremental diff) and re-publish both tabs. The intent
  gate (`is_coding_task`) now fires on build phrases ("create a", "build me",
  "make a", …) as well as the ops verbs.
- **Seeded scenarios** (`website_redesign`) — the scripted `preview_html(False)`
  is the turn-0 artifact: the first mention is a DISPLAY (publish the seed page,
  agent narrates, no regeneration), and *follow-ups edit the seed live* ("make the
  hero background blue" mutates the page + diff). The scripted `/check`→`/patch`→
  `/recap` beats still work as deterministic shortcuts (see below), and `/patch`
  writes its patched page into the live-artifact store so later freeform edits
  build on it.
Mechanics (per the Code Channels + Agent-design docs): the agent emits the
COMPLETE file in a ` ```html ` fence (`agent/htmlblock.py::extract_html`), we diff
it with stdlib `difflib` (`agent/artifact_diff.py::unified`) and publish Code
(`setView type=diff`, distinct `base_branch`/`head_branch`) then Preview
(`setView type=html`, `view_key="preview"`) **sequentially** (they share one
`agent_session_views` array). The reply streams top-level via
`chat.startStream(task_display_mode="plan")` so the persona's ` ```taskplan ` renders
as the collapsible **"Thinking → Thinking completed"** plan block. All of it runs
through ONE engine, `listeners/events/slack_code.py::run_artifact_turn`, called by
session-start (`_run_session_body`'s live-editable branch) AND both follow-up
handlers (`message.py`/`app_mentioned.py`); every externally-visible step is
stop-guarded (`agent_session_stopped` → `slackcode.mark_session_stopped` → no
further `setView`/`setStatus`/`chat.*`). Per-channel current file is held in
`_shared/agent/slackcode.py::_CHANNEL_ARTIFACT` (in-process; a restart drops it).

**"Thinking" plan streams AROUND the work, and status ALWAYS resets (the two UI
bugs, fixed 2026-09-11).** Two rules the spec (Code channel lifecycle PDF, live
`chat.*Stream` docs) makes non-negotiable:
- *The plan spinner must reflect real work, not a post-hoc replay.*
  `run_artifact_turn` now opens the stream and paints the plan steps `in_progress`
  **before** the agent runs (`open_thinking_stream` → `render.render_plan_steps_pending`),
  then flips them to `complete` and closes the stream **after** the file + tabs are
  ready (`finalize_thinking_stream` → `render_plan_steps_complete`). Closing the
  stream (`chat.stopStream`) is what clears the message-level loading indicator, so
  it runs in a `finally`. (The old code ran the agent first, THEN replayed a fake
  0.8s-per-step animation — the "checkmark while still thinking" bug.) The DM path
  (`finalize.py`) keeps the single-shot `render.render_plan` wrapper — unchanged.
- *Session status must return to `active` on every exit* so the "Stop agent"
  spinner (Slack's `processing` UI) can't spin forever. `run_artifact_turn` wraps
  its body in `try/…/finally: if not is_session_stopped: setStatus(active)`; ditto
  `run_patch`. **Exception per spec:** after `agent_session_stopped`, Slack updates
  the status itself, so we call NO `agents.sessions.*` — the finally is guarded by
  `is_session_stopped`.
- `chat.startStream` to a channel REQUIRES `recipient_team_id` (+ `recipient_user_id`);
  `_TopLevelStreamer.start` now sends both (team id via cached
  `slackcode.get_bot_identity` → auth.test; user is the channel participant).
  Without them the stream was silently rejected and stranded the indicator.

**HUMANS + CLAUDE TOGETHER (scripted participants).** A code channel's value is
humans AND the agent in one channel, so each seeded story pulls 1–2 people from
its origin thread into the code channel. Two tiers, both best-effort/stop-guarded:
- **Voice:** the bot posts scripted human chime-ins via `chat:write.customize`
  (`slackcode.post_as_participant`, `chat_postMessage username=<name> icon_url=<real
  avatar>`). Passing BOTH the name AND the persona's real avatar renders each
  chime-in as that person (name + photo); icon_url is required — username alone
  keeps the bot's avatar, and per-message icon_url also keeps successive posts from
  coalescing onto one identity. Only Jennifer types LIVE — these are pre-scripted
  background beats. (NOTE: `slack_read_channel`'s TEXT export mislabels the 2nd
  customized post under the 1st's name — the real Slack UI renders both correctly.
  Trust the UI, not the export.)
- **Presence:** the bot also invites the real person to the roster
  (`slackcode.invite_participants` → `resolve_participant`). Resolution is BY EMAIL,
  not name (name was ambiguous — "Adam" is one user's real_name and another's
  display name). Every demo org uses `demoeng+<stem>_<orgnum>@slack-corp.com`; the
  bot derives `<orgnum>` from its `auth.test` url (`slack-demo-7018.…` → `7018`,
  override `SLACK_DEMO_ORG_NUM`) and resolves via `users.lookupByEmail`, falling
  back to a `users.list` scan by `profile.email`.
- **GRID GOTCHA (why this was hard):** on Enterprise Grid, `users.lookupByEmail` /
  `users.list` REQUIRE a workspace `team_id` (a `T…`) or they fail `missing_argument`
  — and `auth.test` returns the ENTERPRISE id (`E…`), the wrong value. The bot
  captures the workspace `T…` from the inbound event context
  (`slackcode.set_workspace_team_id`, called in app_mentioned/message handlers),
  override `SLACK_WORKSPACE_TEAM_ID`. AND the bot needs `channels:write.invites` /
  `groups:write.invites` to invite others (`channels:join` only joins itself). Silent
  fallback throughout: any failure → skip the invite, the spoofed voices still carry
  it. The audit line reports the outcome (`invited 2/2`, or `0/2 [trace…]`).

Beats are **scenario-authored data**: each seeded module defines `PARTICIPANTS`
(`{name, email_stem, role}`, ≤2) + `participant_beats()` (`{name, phase, text}`,
`phase ∈ {arrival, on_artifacts}`). Voices echo what the person already said in the
origin seed thread. `arrival` fires as the channel opens (the "let's see what
Claude comes back with" stall, over the cold-start first reply); `on_artifacts`
fires just after the diff/dashboard publish (reacting to what shipped). Wired in
`slack_code.py::_post_participant_beats`, called from both `_run_session_body` and
the live-editable branch. Freeform has no cast → no-ops. Slack-ification is
sparing (user: "less is more"): inline emoji in the beat copy + ONE Claude `:eyes:`
reaction on the arrival line (`slackcode.react`). **Four new scopes:
`chat:write.customize` (voice), `users:read.email` (email lookup),
`channels:write.invites` + `groups:write.invites` (invite others to the roster)** —
re-install prompts for them; `run.sh` reconciles the manifest on launch. Launch
`SLACK_CODE_ENABLED=1 SLACK_WORKSPACE_TEAM_ID=T06GCU6GFEK AUDIT_CHANNEL_ID=… ./run.sh
claude` (the team_id override guarantees the Grid invite; without it the bot tries
to capture it from events).

**Model: Haiku for this bot.** `agent/agent.py` passes
`ClaudeAgentOptions(model=CLAUDE_AI_MODEL)`, default `us.anthropic.claude-haiku-4-5`
(env-overridable via `CLAUDE_AI_MODEL`). Scoped to this bot only — the other kit
bots + global `settings.json` keep their `ANTHROPIC_MODEL`. The exact Bedrock
Haiku id is unverified from the sandbox; if the gateway rejects it the CLI 401s
and the turn hangs ~90s — override at launch with `CLAUDE_AI_MODEL=<id>`.

**Scenarios (per-scenario artifact sets).** All artifact content comes from ONE
*scenario fact module* (`scenarios/<slug>.py`) so the diff, preview, dashboard,
and recap can never disagree. Routed by keyword (see ONE AGENT, ALL STORIES above)
or forced with `SLACK_CODE_SCENARIO`; resolved via `scenarios.load_scenario`
(unknown slug → default, never raises). Each scenario declares `ARTIFACTS` (subset
of `{"diff","preview","dashboard"}`); the orchestrator only publishes those (diff
is always published). **Six modules ship (5 scripted + freeform); keep this list in
step with `_KNOWN` in `scenarios/__init__.py`:**
- **`website_redesign`** (DEFAULT) — WelloGuard homepage redesign. `ARTIFACTS =
  {"diff","preview","dashboard"}`, check `contrast`. The **Preview is a REAL
  landing page** (the hero artifact from Slack's launch demos): it ships a
  white-on-amber CTA (~1.9:1, fails AA); `/patch` (or "@Claude fix the contrast")
  darkens the label to near-black (~10.8:1) and the *same Preview tab visibly
  updates*. Also serves the instant marketer **FAQ edit** (see below).
- **`billing_webhook`** — Stripe-webhook→durable-queue refactor. `ARTIFACTS =
  {"diff","dashboard"}` — **no preview** (a backend refactor has no page; an HTML
  preview would be a meta-diagram that undersells it). Check `durability` (the base
  returns 200 to Stripe *before* the enqueue is durable).
- **`checkout_incident`** — P1 checkout-API latency rollback + retry fix.
  `ARTIFACTS = {"diff","dashboard"}`, check `load-test`.
- **`flaky_test`** — the flaky `login_redirect` CI test (condition-wait, not a
  timer bump). `ARTIFACTS = {"diff","dashboard"}`, check `flake-stability`.
- **`sql_migration`** — online-safe `last_login_at` migration (nullable → backfill
  → validate). `ARTIFACTS = {"diff","dashboard"}`, check `migration-safety`.
- **`freeform`** (`SEED = False`) — no scripted artifacts; the agent builds the
  Code/Preview file live from the request and edits it each turn.

**Fast first paint + the instant FAQ edit (2026-09-18).** The first code-diff block
must appear FAST (a few seconds), not after a full ~60s LLM turn — *for the scripted
demo stories, which are the heroes*. Freeform is treated differently on purpose:
- *Scripted stories* (`_run_session_body`) publish the pre-built diff/dashboard
  **before** the agent runs, gated by `SLACK_CODE_PREBUILT_DWELL_S` (default 3s, 0
  disables the dwell) so the just-opened "Thinking" spinner isn't decorative. The
  spinner then **settles to `✓ Thinking` the moment the diff lands** (an early
  `finalize_thinking_header`, `_opened` nulled so the post-agent path skips it) — it
  does NOT keep spinning through the agent turn. The prose reply follows after the
  agent as its own top-level message below the settled diff.
- *Freeform + any unscripted website edit* (`run_artifact_turn`) is **NOT capped** —
  there the diff IS the agent's generated file, so the wait is the real build. The
  "Thinking" spinner stays up showing honest working steps (`_WORKING_PLAN`) and
  settles when the diff lands (`finalize_thinking_stream`). Only `RUN_AGENT_TIMEOUT`
  (agent.py, default 300s) bounds a true hang. (We tried an 18s cap + scaffold here;
  on a slow gateway it showed a stub that the next turn then *edited* instead of
  building the real thing — so the scaffold was dropped. A real snake game at ~28s
  with an honest spinner beats a lying scaffold.)
- *The marketer "Jennifer edit"* — `recognize_faq_edit` (NEAR-EXACT phrase, canonical
  "add an expandable FAQ section to the page") → `run_faq_edit`, `website_redesign`
  only. Serves a pre-baked CSS-only `<details>`/`<summary>` FAQ (`scenario.faq_diff()`
  + `preview_html(patched, faq=True)`) INSTANTLY via `run_scripted_beat` — no LLM turn,
  Claude's response only. It composes with the current `patched` state (doesn't
  silently fix contrast). A drifted/looser phrasing falls through to the live edit path.

Each published tab is one `set_view(view_type=…, view_key=…)` call, keyed so they
coexist:

- **Code** (`diff`) — `scenario.base_diff()` / `patch_diff()`, via
  `set_view(type="diff", content=<unified diff>, base_branch, head_branch)`.
- **Preview** (`html`, only if `"preview" in ARTIFACTS`) — `scenario.preview_html(patched)`,
  a self-contained page (inline `<style>` only, no JS/external assets), via
  `set_view(type="html", view_key="preview", name="Preview", content=<HTML str>)`.
  For `website_redesign` this is the REAL WelloGuard landing page — `patched=True`
  is the same page with the CTA contrast fixed, so re-publishing on the same
  `view_key` visibly updates the tab. This is the video's hero "see what the agent
  is building" moment. A scenario with no page to render omits `"preview"` rather
  than faking a meta-diagram.
- **Dashboard** (`block_kit`) — `scenario.dashboard_blocks()`, via
  `set_view(type="block_kit", view_key="dashboard", name="Dashboard", blocks=<ARRAY>)`.
  block_kit takes the blocks as a real JSON **array** (`blocks=`), NOT a
  `json.dumps`'d string — the string variant is rejected `missing_required_arg`.
  Validated via `blockkit.validate_blocks` first.
- **Recap** — posted as a **formatted message** (`run_make_recap` bolds the 5 `##`
  sections of `scenario.recap_canvas()`), NOT a view tab. The `canvas` view type
  needs an undocumented structural required arg the beta rejected in every
  `/view-probe` variant (even `name`-only → `missing_required_arg`); deferred.

**CONFIRMED setView payload shapes + the CRITICAL sequencing gotcha** (derived
live 2026-09-09 via a `/view-probe` matrix — the beta's per-method doc is
confidential and 404s publicly):
- `name` is the WRITE arg for a view tab's label; `listViews` reads it back as
  `label`. Non-diff tabs need `view_key` + `name`.
- **Publish view tabs SEQUENTIALLY, never in one `asyncio.gather`.** Every
  `setView` mutates the channel's single `agent_session_views` array server-side;
  concurrent calls RACE and only one survives — the bug where the diff landed but
  Preview/Dashboard silently dropped ("only 1 artifact"). `_run_session_body` and
  `run_patch` await each `set_view` one at a time. (Different methods — status,
  context bar, commands — can still share a gather; they don't touch the array.)
- View failures are logged + audited (and, with `SLACK_CODE_VIEW_DEBUG=1`, posted
  in-channel) via `_report_view_results` — never silently swallowed.
- `not_allowed_token_type` from a raw HTTP probe was a red herring: the live bot
  uses the `xoxb` that `slack run` injects as `SLACK_BOT_TOKEN` (minted from the
  `~/.slack` `xoxe` config token), which works — NOT the stale `xoxb` in
  `tokens.json`.

**The check → patch → re-check loop** (the demo's verify peak). The `base_diff`
carries a real, subtle authored flaw (200 is returned *before* the enqueue is
durable). `run_durability_check` posts a scripted FAIL (top-level
`chat.postMessage`, always renders); `run_patch` marks the channel patched
(`slackcode._PATCHED_CHANNELS`), refreshes the Code+Preview tabs to the fixed
state, adds a `durability: pass` context-bar item, and posts the clean PASS.
Deterministic — the agent never computes verdicts. Two triggers, both wired:
- **Slash commands** (safe, zero-LLM): `/check-durability`, `/patch`,
  `/show-metrics`, `/make-recap` in `listeners/commands/__init__.py` (the check
  command is scenario-named `/check-<CHECK_LABEL>`). Registered live via the
  single `set_commands` array in `slack_code.py` (≤10, resent whole).
- **@-mention keywords** (flashy): `recognize_code_action()` maps "run a check",
  "generate a patched version", "make a recap", "is it working" to the same
  helpers, from `_reply_in_code_channel` (app_mentioned) and
  `_reply_in_code_channel_followup` (message). NOTE: runtime slash commands are
  only (re)registered when a session STARTS — an already-open channel from an
  older build won't have new commands, so the mention-keyword path is the
  reliable trigger there.

**Decision provenance.** `scenario.render_provenance()` is prepended to the
first agent turn's input so a later "@Claude why the enqueue-first order?"
answers FROM recorded rationale (a scripted `chat.postMessage` would NOT enter
the resumable SDK session). The session id is now persisted whenever the agent
returns one — not only when the reply was non-empty (fixed a bug where a
slow/empty first reply left no resumable session and the follow-up improvised).

**Adding a new demo scenario:** copy `scenarios/_template.py` → `scenarios/<slug>.py`,
fill its fields (including `ARTIFACTS` — only list tabs the story actually has;
don't force a `"preview"` onto a story with nothing to render), add `<slug>` to
`scenarios/__init__.py::_KNOWN`, set `SLACK_CODE_SCENARIO=<slug>`, add a matching
`channels/seed_<slug>.py` and seed the origin thread, relaunch. No logic changes
— only data. `channels/seed_welloguard_redesign.py` is the reference seed.

**Keep ALL scenarios in sync when you change scenario *shape*.** The five scripted
modules (`website_redesign`, `billing_webhook`, `checkout_incident`, `flaky_test`,
`sql_migration`) are duck-typed against ONE contract (top of `scenarios/__init__.py`
+ `_template.py`) and the orchestrator calls them uniformly — so if you add or
rename a scenario-level function, or add a `_THINKING` / `PARTICIPANTS` key, apply it
to **every** module in `_KNOWN`, not just the one you're demoing. A scenario that
silently omits a member degrades instead of erroring: e.g. `_THINKING["open"]` is
required by `slack_code._scenario_thinking_line(scenario, "open")` on session start,
and the module that was missing it fell back to a generic "Working on it…" spinner
(a real bug, caught 2026-09-18). Run `.venv/bin/python qa.py --self-test` after any
scenario-shape change — it now asserts every scripted scenario defines a non-generic
`"open"` line, and validates the `website_redesign` FAQ artifacts.

**Launch (from your Terminal, not a nested session):**
`SLACK_CODE_ENABLED=1 AUDIT_CHANNEL_ID=C0AUC5B30SE ./run.sh claude`
(defaults to `website_redesign`; add `SLACK_CODE_SCENARIO=billing_webhook` for the
backend story, or `SLACK_CODE_VIEW_DEBUG=1` while iterating to see view failures
in-channel). Confirmed live 2026-09-09: website_redesign renders Code + Preview
(the real WelloGuard page) + Dashboard; the contrast check FAILs, `/patch` visibly
darkens the CTA and PASSes; recap posts as a message. See the parent README
"Scopes → Slack Code" for scopes/feature/events, and `agent/slackcode.py` for the
API wrappers.

## QA

`.venv/bin/python qa.py --self-test` (offline; run anywhere) covers the
blockkit + taskplan extractors/validators and any Slack Code fixtures.
`qa.py --vet-questions` must run from your own Terminal, NOT inside a nested
Claude session (host OAuth vars leak → "Not logged in").
