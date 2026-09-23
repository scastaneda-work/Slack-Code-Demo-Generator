# Slack Code Demo Generator

A demo-building tool for Slack SEs. It generates convincing, fully-scripted
**Slack Code** demos you run in *your own* demo org, tailored to *your*
customer — plus the conversational **Claude Tag** surface when you need it.

The AI coding agent in the channel is a spoof, and **which agent it spoofs is up
to you.** It ships with **Claude as the default persona**, but the persona is
swappable — point it at any AI agent (Cursor, Copilot, Gemini, an in-house
assistant) and the demo presents that agent instead. Agent optionality is the
point: show the story that matches the customer in front of you.

Everything the bot "does" (code diffs, live HTML previews, dashboards, canvas
recaps, human chime-ins) is a **convincing prop** — there is no real repo, no
real GitHub, no real CI. That's the point: the demo lands every time, with no
live dependencies, and you customize the story — and the agent — to the customer
in front of you.

> **New here?** Open this folder in Claude Code and say *"help me set this up"* —
> the bundled `CLAUDE.md` walks your Claude through install, and once you're
> configured, the **`/build-slack-demo`** skill interviews you and builds a
> custom demo story. See **[SETUP.md](SETUP.md)** for the manual steps and
> **[HANDOFF.md](HANDOFF.md)** for the full clone → demo → teardown lifecycle.

## The two surfaces

- **Slack Code** (the main event) — a coding-task @-mention spins up a dedicated
  code channel: a session status, a context bar (repo/branch/PR/CI/check),
  **Artifact tabs** (Code diff, live HTML Preview, Dashboard, Canvas recap), and
  a check → patch → recap lifecycle. Humans and the AI agent appear to
  collaborate in one channel. *Slack Code is a Slack beta — your workspace must
  have it enabled for your app; the bot degrades gracefully to a normal thread
  reply if it isn't.*
- **Claude Tag** — the AI agent as a helpful assistant inside Slack. In DMs / the
  assistant panel it does conversational catch-up and thread/channel
  summarization (decisions made, open questions, who each item is waiting on);
  @-mentioned in a channel it can reply with a rich Block Kit incident/synthesis
  card. Optionally a lightweight in-thread "Thinking" checklist for small
  collaborative tasks.

An SE can demo **either surface or both** — the `/build-slack-demo` interview
asks which you need this time. On both surfaces the on-screen agent is the
swappable persona (Claude out of the box).

## Build your own story (the main event)

The five built-in stories (a WelloGuard homepage redesign, a Stripe webhook
refactor, a checkout-latency incident, a flaky test, a SQL migration) are
**ready-to-run examples and references** — but the real value is customizing to
your customer. Working with your Claude Code, the `/build-slack-demo` skill:

1. asks which surface(s) you're demoing and which AI agent to present (Claude by
   default, or any agent you name);
2. gathers your customer, product, cast, and the technical story;
3. offers a **menu of artifacts** (Code diff, HTML Preview, Dashboard, Canvas
   recap) and suggests ones that fit — the story guides the defaults, you can
   override;
4. authors a deterministic scenario (fast at demo time — no freeform latency)
   plus a matching seed script; and
5. verifies it with `qa.py --self-test` and hands you the launch commands.

## What's in the folder

| Path | What it is |
|---|---|
| `app.py`, `app_oauth.py` | Bot entrypoints (Socket Mode / OAuth HTTP) |
| `run.sh` | Launcher — reads `tokens.json`, launches via the Slack CLI |
| `manifest.json` | Slack app manifest (scopes, Slack Code feature, events) |
| `config.py` | Token loading (chmod-600 guard) + Slack client factories |
| `tokens.example.json` | Copy to `tokens.json` (gitignored) and fill in |
| `.env.sample` | Documented runtime knobs (Slack Code gate, model, org num…) |
| `agent/` | Agent SDK runtime (persona load, render, Slack Code engine) |
| `personas/claude_ai.md` | The default (Claude) agent persona — copy/swap it to spoof a different AI agent |
| `personas/_demo_context.example.md` | Per-demo channel context template (the skill writes `_demo_context.md`) |
| `scenarios/` | Slack Code stories (5 examples + `freeform` + `_template.py`) |
| `scenarios_tag/` | Claude Tag lightweight stories (+ `_template.py`) |
| `channels/` | Seed scripts that stage a story's origin thread |
| `qa.py` | Offline self-test — validates every scenario against the contract |
| `.claude/skills/build-slack-demo/` | The guided demo-builder skill |
| `SETUP.md`, `HANDOFF.md`, `CLAUDE.md` | One-time setup / lifecycle / Claude-Code guidance |

## Security & scope

- **No real integrations.** Artifacts are props; no repo, GitHub, or CI is touched.
- **Your credentials stay local.** `tokens.json` (gitignored, chmod 600) holds
  your app's bot/app tokens. By default the model runs through *your* `claude`
  CLI login — no API keys live in this repo. (The persona the agent *presents* as
  is separate from the model driving it.)
- **Ships with no live-org data.** The example stories use fictional companies
  and people; you supply your own org's IDs in `tokens.json`.

## Requirements

- Python **3.10+** (3.12 recommended)
- The **Slack CLI** (`slack`), logged in to your demo org
- A local **`claude` CLI** login (or set `ANTHROPIC_API_KEY`)
- Slack Code demos additionally need the **Slack Code beta** enabled for your app
