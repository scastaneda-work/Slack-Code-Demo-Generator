---
description: Check this demo kit's setup health (wraps ./demo check).
allowed-tools: Bash(./demo check)
---

Run the demo kit's health check and read the result back in plain language:

!`./demo check`

Summarize what passed and what didn't. For anything that isn't ✓, say in one
line what it means and the single next step to fix it:
- missing/unreadable `tokens.json` → walk through SETUP.md from the top.
- venv/Python/dep problem → the exact `pip install` line for their own
  Terminal (PyPI is often blocked in sandboxed sessions, so don't run it here).
- a specific demo flagged unhealthy → `./demo show <id>` to see what it's
  missing, then say so.

Translate the raw output rather than dumping it verbatim. If everything's
green, say so in one line and point the SE at `/demo` to pick a story to run.
