---
description: List, switch, or create the AI agent persona the bot spoofs (wraps ./demo persona).
argument-hint: "[list | use <slug> | new <Name>]"
allowed-tools: Bash(./demo persona *)
---

Current personas available to spoof:

!`./demo persona list`

The SE said: "$ARGUMENTS"

- "what personas are there" / "list them" → the list above; summarize it.
- "switch to `<name>`" / "use `<name>`" / "make it `<name>` instead of Claude"
  → match against the list above (fuzzy is fine, e.g. "github" → the GitHub
  slug) and run `./demo persona use <slug>`. Confirm the switch and remind the
  SE to restart the bot (Ctrl+C, then `SLACK_CODE_ENABLED=1 ./run.sh` again in
  their own Terminal) — it doesn't hot-reload.
- "make a new persona for `<agent>`" / "add `<name>`" → you need both a display
  name and who makes it (e.g. "Cursor" / "Anysphere"); ask for the maker if the
  SE didn't give one, then run `./demo persona new "<Name>" --maker "<Maker>"`
  and tell them which file it wrote (`personas/<slug>.md`) so they can tune the
  voice.

Switching only changes which AI agent the in-channel bot impersonates — it
never changes which model answers. If a demo is actively running, confirm
before switching. When editing a persona file yourself, Slack mrkdwn bold is
single-asterisk (`*bold*`), never `**bold**`.
