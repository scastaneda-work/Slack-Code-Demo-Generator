# Setup — one-time per SE / per demo org

This is the one-time setup to get the Slack Code Demo Generator running in
**your** demo org. Do it once; after that, you build and run demos with the
`/build-slack-demo` skill (see [HANDOFF.md](HANDOFF.md)).

Each step is tagged **[Claude Code]** (your Claude Code session can run it) or
**[Terminal]** (you run it yourself — long-running processes, browser sign-in,
or anything that needs your real shell). If you opened this folder in Claude
Code, just say *"help me set this up"* and it will walk these with you.

> **Audience note for Claude Code:** the person you're helping is a Slack SE —
> fluent in Slack, comfortable in a demo, but may not be a coder and may rarely
> use a terminal. Narrate what each step does, run the **[Claude Code]** steps
> for them, and hand them the **[Terminal]** ones as copy-paste blocks. Never
> ask them to paste a token into chat (see Step 4).

## Prerequisites

- **Python 3.10+** (3.12 recommended). Check: `python3.12 --version`.
- **Slack CLI** installed and logged in to your demo org (`slack login`).
- A local **`claude` CLI** login, OR an `ANTHROPIC_API_KEY` (Step 5).
- For Slack Code demos: your workspace admin must have the **Slack Code beta**
  enabled for your app (ask your Slack contact; the bot still runs without it,
  degrading Slack Code to normal thread replies).

## Step 1 — Create your Slack app from the manifest **[Terminal]**

From this folder, create a new app in your demo org using `manifest.json`:

```bash
slack create claude-in-slack-demo --template .
```

Or create it in the Slack app dashboard (api.slack.com → Create New App → From a
manifest) by pasting `manifest.json`. This registers the scopes, the Slack Code
feature block, and the event subscriptions the bot needs.

## Step 2 — Install the app to your demo org **[Terminal]**

```bash
slack install
```

Approve the requested scopes (they include `chat:write.customize`,
`channels:write.invites`, `users:read.email`, `code_channels:manage`,
`canvases:write` — these power the spoofed human chime-ins, roster invites, and
Slack Code artifacts). Note the resulting **app id** (`A0…`), **bot user id**
(`U0…`), **bot token** (`xoxb-…`), and **app-level token** (`xapp-…`).

## Step 3 — Create `tokens.json` **[Claude Code]** (values) / **[Terminal]** (secrets)

Copy the template and fill it in:

```bash
cp tokens.example.json tokens.json
```

`team_id`, `workspace_id`, `audit_channel_id`, the app id, and the bot user id
are **not secrets** — fine to set in `tokens.json` directly (Claude Code can do
this for you). The **bot token** (`xoxb-…`) and **app token** (`xapp-…`) ARE
secrets: paste them into `tokens.json` yourself, never into chat.

Then lock the file down:

```bash
chmod 600 tokens.json
```

`config.py` refuses to load `tokens.json` unless it's `chmod 600` (bypass once
during setup with `CLAUDE_DEMO_SKIP_TOKEN_PERM_CHECK=1`).

## Step 4 — Token discipline **[Claude Code]**

Claude Code: never ask the SE to paste a `xoxb-`/`xapp-` token into the chat.
Direct them to edit `tokens.json` in their editor. Non-secret IDs (`team_id`,
`workspace_id`, `audit_channel_id`, app id, bot user id) are fine to handle in
chat.

## Step 5 — Python environment **[Terminal]**

Build the venv with **Python 3.12** (the Agent SDK requires 3.10+; an older
`python3` fails with "requires a different python version"):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Model auth: if you have a local `claude` CLI login, nothing more is needed —
`run.sh` uses it. (Internal SEs whose `~/.claude/settings.json` carries a
Bedrock gateway env get it picked up automatically. Anyone else can instead set
`ANTHROPIC_API_KEY` in a `.env` — see `.env.sample`.)

## Step 6 — Verify **[Terminal]**

```bash
.venv/bin/python qa.py --self-test
```

Expect all `[PASS]` and exit 0 — this proves the engine and all example
scenarios are intact. (One `RuntimeError: boom` line mid-run is an *intentional*
injected-failure test, immediately followed by a `[PASS]`.)

## You're set

Next: build a demo. In Claude Code, run **`/build-slack-demo`** (or say "build
me a demo for <customer>"). To run a built-in example instead, see
[HANDOFF.md](HANDOFF.md) → "Run a demo".

---

### Sandbox gotchas (running setup *inside* Claude Code)

- **`www.slack.com` vs `slack.com`.** Claude Code's sandbox proxy allows
  `*.slack.com` but not the bare apex — `config.py` already uses
  `https://www.slack.com/api/`. Don't "fix" this by editing sandbox settings.
- **PyPI is blocked in-sandbox.** Run the `pip install` (Step 5) in your own
  Terminal, not through sandboxed Bash.
- **Host OAuth vars leak into nested sessions.** Run any live Slack call
  (`qa.py --vet-questions`, seeding, `run.sh`) from your own Terminal, not a
  nested Claude session, or it may report "Not logged in".
