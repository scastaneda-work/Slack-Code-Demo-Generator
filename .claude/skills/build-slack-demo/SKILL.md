---
name: build-slack-demo
description: Use when a Slack SE wants to build or customize a Claude-in-Slack demo for a specific customer — a Slack Code story (code channel with diff/preview/dashboard/canvas artifacts) and/or a Claude Tag story (in-thread checklist). Triggers on "build a demo", "customize this for <customer>", "make a Slack Code story", "new demo scenario". Interviews the SE, then authors a deterministic scenario + seed script and verifies it with qa.py --self-test.
---

# Build a Slack demo

You are helping a Slack SE build a **custom, deterministic** Claude-in-Slack demo
for their customer. Deterministic means: authored ahead of time as data, so it
renders instantly at demo time (no freeform LLM latency). You produce data files
that satisfy a QA-enforced contract, then prove them with `qa.py --self-test`.

Read this whole file, then run the interview. Keep the SE in plain language —
they may not be a coder. Never fabricate their org's real names/emails — ask.

## Step 0 — Which surface(s)?

Ask first: **"Are you demoing Slack Code, Claude Tag, or both this time?"**

- **Slack Code** — a code channel with artifact tabs (the bulk of demos). Author
  a `scenarios/<slug>.py`.
- **Claude Tag** — a lightweight in-thread "Thinking" checklist collaboration.
  Author a `scenarios_tag/<slug>.py`.
- **Both** — do both, but they're independent files; the SE can run either.

Only do the path(s) they pick. If both, build the Slack Code one first (it's the
hero), then the Tag one.

## Step 1 — Gather the story (interview, one topic at a time)

Ask, don't assume:

1. **Customer / company** name and what they do (used for the fictional company
   in the story — or use the customer's real name if the SE wants).
2. **Product / domain** the demo centers on.
3. **The technical change** Claude will appear to make (a redesign, a refactor,
   an incident fix, a migration, a new feature…).
4. **The cast** — 1–2 people who appear in the origin thread (name + role).
   Names alone are enough: every line is posted through the bot spoofing that
   name + avatar, so the backstory renders in any org without those people
   existing. Then offer the OPTIONAL roster invite, in these words:
   > *"I can also try to invite the real people to the channel roster so they
   > appear as members — but I may need your help identifying the right users. I
   > look them up by email (`demoeng+<stem>_<orgnum>@slack-corp.com`). Want me to
   > attempt that? If so, tell me each person's email stem; if you'd rather not,
   > we'll skip it and the scripted voices carry the demo."*
   Only collect email stems if the SE opts in. Never guess an email — ask. If any
   don't resolve at run time they're skipped silently.
5. **The flaw → check → patch beat** (Slack Code): what subtle problem does the
   first version carry, what check catches it, and what does the fix change? This
   is the demo's verify peak — make it concrete and believable.

## Step 2 — The artifact menu (Slack Code only)

Present the menu and recommend based on the story. The SE can override.

- **Code diff** — always included. A unified diff (the "before" with the flaw).
- **HTML Preview** — a real rendered page/output. Include when the change is
  *visual* (a landing page, a UI). A backend refactor has no page — omit it
  rather than fake a diagram.
- **Dashboard** — Block Kit KPI cards (before → after metrics). Include when the
  story has numbers worth showing (conversion, latency, error rate).
- **Canvas recap** — a live Canvas "deck" summarizing the work (5 sections).
  Great for the "is leadership behind it" beat. *Mention Canvas explicitly — SEs
  often don't know it's an option.* It publishes as a real canvas tab (falls back
  to a formatted message if the canvas API path fails).

`ARTIFACTS` in the scenario = the subset you include (always contains `"diff"`).
Let the story drive the default; confirm with the SE.

## Step 3 — Author the Slack Code scenario

Copy `scenarios/_template.py` → `scenarios/<slug>.py` and fill EVERY member. The
contract (duck-typed, enforced by `qa.py --self-test`):

Required always: `SLUG` (== module name == `_KNOWN` entry), `REPO`, `REPO_URL`,
`BRANCH_PREFIX`, `PR_URL`, `CHECK_LABEL`, `ARTIFACTS` (set, contains `"diff"`).

Required for a scripted (seeded) scenario:
- `base_diff() -> str` (starts `"diff --git"`) — author the flaw here.
- `patch_diff() -> str` — differs from base; closes the flaw.
- `check_fail_report() -> str` — Slack mrkdwn, contains `:red_circle:` and the
  word "patch"; names the flaw, offers to patch.
- `check_pass_report() -> str` — contains `:large_green_circle:`.
- `recap_canvas() -> str` — EXACTLY five `## ` sections; no `**` bold.
- `provenance() -> dict[str,str]` (≥2 entries) and `render_provenance() -> str`
  (the dict flattened to `- key: why` lines).
- `thinking_line(action)` for `check/patch/metrics/recap`, backed by a `_THINKING`
  dict that MUST include a non-generic `"open"` line (the session-start spinner).
- `PARTICIPANTS: list[dict]` (1–2, each `{name, email_stem, role}`) and
  `participant_beats() -> list[dict]` (each `{name, phase, text}`,
  `phase ∈ {"arrival","on_artifacts"}`, ≥1 `arrival`, no `**`).

Conditional on `ARTIFACTS`:
- if `"dashboard"`: `dashboard_blocks() -> list[dict]` (valid Block Kit — validate
  with `blockkit.validate_blocks`). Pull the numbers from ONE module-level
  constant the recap also reads, so they can't disagree.
- if `"preview"`: `preview_html(patched: bool=False) -> str` — ONE self-contained
  HTML doc (starts `<!doctype`, no `http://`/`https://`/`<script`), and it must
  differ between `preview_html(False)` and `preview_html(True)`. A previewable
  scenario also needs `seed_filename() -> str` (non-empty).

Then register the slug:
- add `"<slug>"` to the `_KNOWN` set in `scenarios/__init__.py`;
- add a `("<slug>", ("keyword", "phrases", ...))` entry to `_ROUTE_KEYWORDS` in
  the same file — put SPECIFIC phrases before generic ones. The trigger phrase in
  the seed must contain a routing keyword AND an intent keyword (so
  `is_coding_task` opens a channel — e.g. "implement", "build", "fix", "refactor",
  "roll back", "migrate").

Study `scenarios/website_redesign.py` (preview + dashboard + FAQ) and
`scenarios/billing_webhook.py` (diff + dashboard, no preview) as worked references
before writing. Slack mrkdwn bold is `*single asterisks*`.

## Step 4 — Author the seed script (bot-spoofed)

Copy `channels/seed_welloguard_redesign.py` → `channels/seed_<slug>.py` and adapt:
- Set `CHANNEL_NAME`, the `CAST` (names + roles; add an `email_stem` per person
  ONLY if the SE opted into roster invites in Step 1.4 — otherwise omit it), and
  `MESSAGES` — a short believable origin thread that plants the flaw and ENDS with
  a line @-mentioning the bot with your routing keyword ("<@BOT>" is replaced with
  the real bot id at runtime).
- Every line posts THROUGH THE BOT via `chat:write.customize` (username +
  optional icon_url) — do NOT require per-persona user tokens.
- The roster invite is behind the `--invite` flag (OFF by default) and only runs
  when the SE passes it. Keep that gate; don't invite by default.
Keep it valid Python.

## Step 5 — (If building Claude Tag) author the Tag scenario

Copy `scenarios_tag/_template.py` → `scenarios_tag/<slug>.py`, fill `SLUG`,
`KEYWORDS` (a tuple — MUST stay disjoint from the Slack Code keywords so a build
task isn't stolen into the Tag surface), `COMMITMENT`, `OPEN_IN_CLAUDE_URL`,
optional `PARTICIPANTS`, `initial_plan()` (a taskplan-shaped checklist, ≤8 steps),
`turn_deltas()`, and `result_line()`. Add `"<slug>"` to `_KNOWN` in
`scenarios_tag/__init__.py`. Study `scenarios_tag/scheduled_exports.py` and
`scenarios_tag/blog_update.py`.

## Step 6 — Verify

Run (in the SE's Terminal — see the sandbox notes in SETUP.md):

```bash
.venv/bin/python qa.py --self-test
```

It runs the full contract loop over every slug in `_KNOWN`, including your new
one. Fix anything it flags and re-run until exit 0 with no `[FAIL]`. Common
misses: dashboard/recap numbers disagree; `recap_canvas` isn't exactly 5 `##`
sections; a `**` bold slipped in; `_THINKING["open"]` is generic or missing;
`preview_html` isn't self-contained or doesn't change when patched.

## Step 7 — Hand off the run commands

Tell the SE exactly how to run it (in their own Terminal):

```bash
.venv/bin/python channels/seed_<slug>.py --create          # stage the backstory (spoofed voices)
# add --invite to ALSO try adding the real cast to the roster (only if they opted in):
#   .venv/bin/python channels/seed_<slug>.py --create --invite
SLACK_CODE_ENABLED=1 ./run.sh
```

If they opted into invites, remind them the `--invite` pass looks people up by
email and silently skips anyone it can't find — tell them which names resolved so
they can decide whether to add the rest by hand in Slack.

Then @-mention the bot in the seeded thread with the trigger phrase. Remind them
Slack Code needs the beta enabled for their app; without it, the bot degrades to
a normal thread reply. For a Tag-only demo, launch without `SLACK_CODE_ENABLED`
and @-mention the bot in a normal channel thread (Tag follow-ups must @-mention).
