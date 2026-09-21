import re

from slack_bolt.async_app import AsyncApp

from .agent_session_stopped import handle_agent_session_stopped
from .app_home_opened import handle_app_home_opened
from .app_mentioned import handle_app_mentioned
from .assistant_thread_started import handle_assistant_thread_started
from .code_channel_action import handle_code_channel_action
from .message import handle_message


def register(app: AsyncApp):
    app.event("app_home_opened")(handle_app_home_opened)
    app.event("app_mention")(handle_app_mentioned)
    app.event("assistant_thread_started")(handle_assistant_thread_started)
    app.event("message")(handle_message)
    # Slack Code stop signal (gracefully no-ops if the beta never fires it).
    app.event("agent_session_stopped")(handle_agent_session_stopped)
    # Slack Code interactive context-bar item click. NOTE: `code_channel_action`
    # is NOT a subscribable bot event — the manifest validator rejects it in
    # bot_events (invalid_user_event_types). So it is delivered as an INTERACTIVITY
    # payload (block_actions-style over the interactions endpoint / Socket Mode),
    # which needs no event subscription, only interactivity enabled (it is). We
    # register it as an action; the exact action_id / payload shape is confirmed
    # from a live click (the handler logs the raw payload). Registered defensively
    # under a couple of plausible identifiers until the shape is captured.
    # A catch-all matcher for any action_id we DON'T already handle, so an unknown
    # code-channel item click can't be silently dropped before we've captured its
    # shape. The negative-lookahead excludes the real buttons (feedback /
    # slackcode_archive) so this never steals them; registered AFTER them so
    # Bolt's in-order matching lets the specific handlers win first anyway.
    _CAPTURE_RE = re.compile(r"^(?!feedback$|slackcode_archive$).+")
    try:
        app.action(_CAPTURE_RE)(handle_code_channel_action)
    except Exception:  # noqa: BLE001 — tolerate a Bolt version that rejects the matcher
        pass
