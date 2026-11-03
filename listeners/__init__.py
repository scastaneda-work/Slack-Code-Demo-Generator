import logging

from slack_bolt.async_app import AsyncApp

from listeners import actions, commands, events, views
from listeners.events import code_channel_action

# Bump on every meaningful edit so a fresh boot proves the running process has
# the latest code (Bolt/slack run has no hot reload — a stale process silently
# ignores edits). Grep the boot log for this line to confirm the restart took.
_BUILD_TAG = "slack-code-artifacts-18 (confirmed live 2026-11-02: a context-bar action chip click is a Bolt slash_commands request, not code_channel_action/block_actions — added a catch-all app.command() listener in code_channel_action.py, registered AFTER commands.register() so literal /patch /create-pr etc. still win first)"


def register_listeners(app: AsyncApp):
    logging.getLogger("claude-ai-bot").warning("register_listeners: build=%s", _BUILD_TAG)
    actions.register(app)
    commands.register(app)
    # MUST come after commands.register() — see code_channel_action.register()'s
    # own docstring for why the ordering matters.
    code_channel_action.register(app)
    events.register(app)
    views.register(app)
