from logging import Logger

from slack_bolt.context.set_suggested_prompts.async_set_suggested_prompts import (
    AsyncSetSuggestedPrompts,
)

SUGGESTED_PROMPTS = [
    {"title": "Summarize a thread", "message": "Can you summarize this thread for me?"},
    {"title": "Draft a message", "message": "Help me tell my team we're pushing the demo to next week"},
    {"title": "Brainstorm ideas", "message": "Brainstorm 5 names for our new onboarding tool"},
    {"title": "Explain a concept", "message": "Explain what a vector database is"},
]


async def handle_assistant_thread_started(
    set_suggested_prompts: AsyncSetSuggestedPrompts, logger: Logger
):
    """Handle assistant thread started events by setting suggested prompts."""
    try:
        await set_suggested_prompts(
            prompts=SUGGESTED_PROMPTS,
            title="How can I help you today?",
        )
    except Exception as e:
        logger.exception(f"Failed to handle assistant thread started: {e}")
