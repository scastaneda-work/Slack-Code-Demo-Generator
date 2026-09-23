# CLAUDE.md — guidance for Claude Code operating this repo

This repo is the **Slack Code Demo Generator**: a demo-building tool a Slack SE
runs in their own demo org to generate scripted **Slack Code** demos (plus the
**Claude Tag** surface), customized to their customer. The AI coding agent in
the channel is a spoof — it ships with **Claude as the default persona**, but
the persona is swappable to spoof any AI agent (Cursor, Copilot, Gemini, etc.).
You (Claude Code) are here to help the SE set it up, build a custom demo story,
run it, and tear it down.

Read [README.md](../README.md) for what it is, [SETUP.md](../SETUP.md) for the
one-time install, and [HANDOFF.md](../HANDOFF.md) for the lifecycle.

## Audience posture

The person you're helping is a **Slack SE** — fluent in Slack and in demoing,
but may not be a coder and may rarely use a terminal. Plan for the least
experienced case: narrate what each step does in plain language, run the steps
you safely can, and hand the SE copy-paste blocks for the ones that need their
own Terminal (long-running processes, browser sign-in, secret entry). Don't
assume they'll read the code.

## First-run detection

At the start of a session, check whether `tokens.json` exists:

- **No `tokens.json`** → the SE hasn't set up yet. Offer to walk them through
  [SETUP.md](../SETUP.md) (create the app, install, capture tokens, venv,
  self-test).
- **`tokens.json` present** → they're configured. Offer to **build a demo**
  (`/build-slack-demo`) or run an existing one. Don't re-run setup.

## Building demos

The heart of this tool is the **`build-slack-demo`** skill
(`.claude/skills/build-slack-demo/`). When the SE wants a new or customized demo
— "build a demo for <customer>", "customize this for <product>", "make a Slack
Code story about <X>" — invoke it. It interviews them (which surface; the
customer/cast/story; the artifact menu) and writes a deterministic scenario +
seed script, then verifies with `qa.py --self-test`. Prefer it over hand-editing
scenario files.

## Token discipline

- **Never** ask the SE to paste a `xoxb-`/`xapp-` token into the chat. Route
  secrets into `tokens.json` in their editor.
- Non-secret IDs (`team_id`, `workspace_id`, `audit_channel_id`, app id, bot
  user id) are fine to handle in chat and write to `tokens.json` for them.
- `config.py` enforces `chmod 600` on `tokens.json`.

## Things to get right

- **Slack mrkdwn bold is single-asterisk** `*bold*` — never `**bold**` (renders
  as literal asterisks in Slack). Applies to personas, scenarios, canvases.
- **Slack Code is a beta.** It needs the SE's workspace to have it enabled for
  their app. Without it, the bot degrades to a normal thread reply — say so
  rather than implying it's broken.
- **Run live steps in the SE's own Terminal**, not sandboxed Bash or a nested
  session: `pip install` (PyPI is often blocked in-sandbox), seeding, `run.sh`,
  and `qa.py --vet-questions` (host OAuth vars leak into nested sessions → "Not
  logged in").
- **Build the venv with Python 3.12** (3.10+ required by the Agent SDK).
- **After changing any scenario shape**, run `.venv/bin/python qa.py --self-test`.

## Verify before claiming done

For any scenario you author or edit, run `qa.py --self-test` and confirm exit 0
before telling the SE it's ready. Artifacts are props — never imply a real repo,
GitHub, or CI is involved.
