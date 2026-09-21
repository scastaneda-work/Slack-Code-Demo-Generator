from slack_bolt.async_app import AsyncApp

from .feedback_buttons import handle_feedback_button
from .slack_code_archive import handle_slack_code_archive


def register(app: AsyncApp):
    app.action("feedback")(handle_feedback_button)
    # Slack Code: archive-on-confirm button on a session's wrap-up summary.
    app.action("slackcode_archive")(handle_slack_code_archive)
