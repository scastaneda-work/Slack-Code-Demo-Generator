# Handoff — the demo lifecycle

How a Slack SE takes this from a fresh clone to a live, customized demo and back.
Read [README.md](README.md) for what the tool is and [SETUP.md](SETUP.md) for the
one-time install detail.

## Lifecycle at a glance

```
clone ──▶ setup (once per demo org) ──▶ build a demo ──▶ run it live ──▶ tear down
                                          (repeat build→run per customer)
```

### 1. Clone

```bash
git clone <your-fork-of-this-repo> claude-in-slack-demo-simulator
cd claude-in-slack-demo-simulator
```

Open the folder in Claude Code — the bundled `CLAUDE.md` primes your session to
help. On first open, if there's no `tokens.json`, it walks you through
[SETUP.md](SETUP.md); once configured, it offers to build a demo.

### 2. Setup (once per demo org)

Follow [SETUP.md](SETUP.md): create the app from `manifest.json`, install it,
capture tokens into `tokens.json` (`chmod 600`), build the venv (Python 3.12),
and run `qa.py --self-test`.

### 3. Build a demo — the `/build-slack-demo` skill

In Claude Code, run:

```
/build-slack-demo
```

(or just say *"let's build a demo for <customer>"*). It interviews you —
which surface (Slack Code, Claude Tag, or both), the customer/product/cast, the
technical story, and which artifacts to show (Code diff, HTML Preview,
Dashboard, Canvas recap) — then writes a deterministic scenario + a matching
seed script and verifies them with `qa.py --self-test`. Because the story is
authored ahead of time, it renders instantly at demo time (no freeform LLM wait).
The spoofed agent defaults to Claude; swap the persona if the customer wants to
see a different AI agent in the channel.

Prefer a built-in example? The five shipped stories run as-is; the default is
`website_redesign`.

### 4. Run a demo **[your Terminal]**

Seed the origin thread, then launch the bot:

```bash
.venv/bin/python channels/seed_<your-slug>.py --create
SLACK_CODE_ENABLED=1 ./run.sh
```

Then in Slack, @-mention the bot in the seeded thread with the trigger phrase.
For a Claude-Tag-only demo, launch without `SLACK_CODE_ENABLED` and DM the bot
or @-mention it in a channel.

Optional env (see `.env.sample`): `AUDIT_CHANNEL_ID` (log actions to a channel),
`DEMO_ADMIN_NAME` (mask a live admin's name in audit posts),
`SLACK_WORKSPACE_TEAM_ID` (guarantees roster invites on Enterprise Grid),
`SLACK_DEMO_ORG_NUM` (persona-email lookups).

### 5. Tear down

The Slack Code artifacts are ephemeral (per-channel, in-process — a bot restart
drops them). To clean up what seeding created, archive or delete the demo
channel in Slack:

```bash
# archive the seeded channel (reversible); or delete it from the Slack UI
slack api conversations.archive --channel <C0…>
```

Custom scenarios/seed scripts you generated stay in the repo for reuse; delete
the `scenarios/<slug>.py`, `scenarios_tag/<slug>.py`, and `channels/seed_<slug>.py`
files (and their `_KNOWN`/`_ROUTE_KEYWORDS` entries) if you want them gone.

## What NOT to commit

`tokens.json`, `.env`, `.slack/apps*.json`, `*_state.json`, and
`personas/_demo_context.md` are gitignored — keep them that way. Before pushing,
run the secret check:

```bash
git grep -nE 'xox[bp]-|xapp-' -- . ':!*.example.json' ':!*.md'
```

It should return nothing but placeholders. Also confirm no live-org IDs snuck
into a custom scenario:

```bash
git grep -nE 'xoxb-[A-Za-z0-9]|xapp-[A-Za-z0-9]'
```

## Getting help

Open the folder in Claude Code and ask. The `.claude/CLAUDE.md` in this repo
tells your Claude how to operate the tool, and `/build-slack-demo` is always the
path to a new story.
