import logging

from slack_bolt.async_app import AsyncApp

from listeners import actions, commands, events, views

# Bump on every meaningful edit so a fresh boot proves the running process has
# the latest code (Bolt/slack run has no hot reload — a stale process silently
# ignores edits). Grep the boot log for this line to confirm the restart took.
_BUILD_TAG = "slack-code-artifacts-17 (canvas CRACKED: recap now a living canvas tab via canvases.create→setView(canvas_id), edit-in-place via canvases.edit, message fallback; code_channel_action captured via catch-all action matcher; cap guards + agent_resource/summary_message + rename/live-summary lifecycle)"


def register_listeners(app: AsyncApp):
    logging.getLogger("claude-ai-bot").warning("register_listeners: build=%s", _BUILD_TAG)
    actions.register(app)
    commands.register(app)
    events.register(app)
    views.register(app)
