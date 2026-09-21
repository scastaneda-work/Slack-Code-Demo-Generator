def build_app_home_view(
    install_url: str | None = None, is_connected: bool = False
) -> dict:
    """Build the App Home Block Kit view.

    Args:
        install_url: OAuth install URL. When provided, the user has not
            connected and will see a link to install.
        is_connected: When ``True``, the user is connected and the MCP
            status section shows as connected.
    """
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":wave: Hey, I'm Claude — your AI teammate in Slack",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "Send me a *direct message* or *mention me in a channel* to get started — "
                    "no need to leave the conversation."
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*What I can help with*\n"
                    "• :speech_balloon: *Catch you up* — decisions made, what's still open, "
                    "and who each item is waiting on\n"
                    "• :memo: *Draft a message* — a status update, a note to your team, anything "
                    "that needs the right tone\n"
                    "• :bulb: *Brainstorm* — names, ideas, angles on a problem\n"
                    "• :rotating_light: *Triage an incident* — pull together a summary and next steps"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Tip: mention me in a thread with “catch me up” and I'll pull it together.",
                }
            ],
        },
        {"type": "divider"},
    ]

    if is_connected:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\U0001f7e2 *Slack MCP Server is connected.*",
                },
            }
        )
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "The agent can search messages, read channels, and more.",
                    }
                ],
            }
        )
    elif install_url:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"\U0001f534 *Slack MCP Server is disconnected.* <{install_url}|Connect the Slack MCP Server.>",
                },
            }
        )
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "The Slack MCP Server enables the agent to search messages, read channels, and more.",
                    }
                ],
            }
        )
    else:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\U0001f534 *Slack MCP Server is disconnected.* <https://github.com/slack-samples/bolt-python-starter-agent/blob/main/claude-agent-sdk/README.md#slack-mcp-server|Learn how to enable the Slack MCP Server.>",
                },
            }
        )
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "The Slack MCP Server enables the agent to search messages, read channels, and more.",
                    }
                ],
            }
        )

    return {
        "type": "home",
        "blocks": blocks,
    }
