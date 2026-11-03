---
description: List, show, run, or route a Slack Code demo via natural language (wraps ./demo).
argument-hint: "[list | show <name> | run <name> | route <text>]"
allowed-tools: Bash(./demo *)
---

Here are the demos currently in this repo:

!`./demo list`

The SE said: "$ARGUMENTS"

Work out which `./demo` subcommand they mean and run it:
- Browsing / "what have I got" / "pull up the demos" → the list above; just
  summarize it, don't re-run `./demo list`.
- "show me `<name>`" / "what's in `<name>`" → `./demo show <name>`.
- "run `<name>`" / "start the `<name>` demo" / "post the backstory for `<name>`"
  → `./demo run <name> post-backstory`. If the action name isn't obvious, run
  `./demo show <name>` first to see its actions, then run the right one.
- "which demo fits `<customer ask>`" → `./demo route "<their text>"`, then offer
  to run the match.

Match the demo name against the list above even if the SE gave a partial or
fuzzy name (e.g. "the checkout one" → `checkout-incident`); if more than one
could match, ask which. If nothing matches, say so and show the list rather
than guessing — never invent a demo id.

Some `run` actions are destructive or re-posts (the command will say so and
ask for `--yes`); tell the SE what it does before adding `--yes` yourself.
Starting the bot (`SLACK_CODE_ENABLED=1 ./run.sh`) is a long-lived process —
don't run it here; hand the SE that exact command for their own Terminal, and
remind them not to close that window during the demo.
