"""Shared post-`run_agent` rendering for the message and app_mention handlers.

Both handlers do the same thing once they have the agent's response text:
optionally animate a taskplan (timeline task cards / plan block), then post the
answer as either a Block Kit card or streamed prose, with feedback buttons. This
module is the single place that logic lives so the two listeners can't drift.

The ```taskplan``` capability is always present in the runtime (zero cost when
unused, exactly like the ```blockkit``` parser). Whether the bot ever EMITS a
task plan is decided entirely by the persona: include the optional taskplan
section in personas/claude_ai.md to turn it on, omit it to leave the bot
plain-loading like before.
"""

from logging import Logger

from slack_bolt.context.say.async_say import AsyncSay
from slack_bolt.context.say_stream.async_say_stream import AsyncSayStream
from slack_sdk.web.async_client import AsyncWebClient

from agent.audit import audit_log
from agent.blockkit import extract_blocks, validate_blocks
from agent.render import render_plan
from agent.taskplan import extract_plan, validate_plan
from listeners.views.feedback_builder import build_feedback_blocks


async def finalize_response(
    *,
    response_text: str,
    client: AsyncWebClient,
    say: AsyncSay,
    say_stream: AsyncSayStream,
    logger: Logger,
    user_id: str | None,
    channel_id: str,
    thread_ts: str,
) -> str:
    """Render the agent's reply. Returns a tier label for audit logging.

    The reply may carry up to two fences: an optional leading ```taskplan```
    (the animated loading steps — valid in DMs and channels alike, it's loading
    UX not an answer card) and the existing optional ```blockkit``` answer card.
    When neither is present, behavior matches the pre-taskplan bot exactly.
    """
    # 1. Peel off the optional taskplan fence; `rest` is the answer body.
    plan, rest = extract_plan(response_text)
    if plan is not None:
        plan_violations = validate_plan(plan)
        if plan_violations:
            # Plan declared but malformed — drop it silently and render the answer
            # normally. A bad loading animation should never break the reply.
            logger.warning("taskplan invalid, skipping animation: %s", plan_violations)
            plan = None

    # 2. Parse the answer body for a Block Kit card.
    card_blocks, tier = extract_blocks(rest)

    if tier == "parse_error":
        await say(text="Sorry, I botched the card formatting. Try again?", thread_ts=thread_ts)
        await audit_log(
            client,
            f":warning: Claude AI→<@{user_id}> in <#{channel_id}> "
            f"persona=`claude_ai` blockkit_parse_error len={len(response_text)}",
        )
        return "parse_error"

    if card_blocks is not None:
        violations = validate_blocks(card_blocks)
        if violations:
            await say(text="That card got too long, let me try again.", thread_ts=thread_ts)
            await audit_log(
                client,
                f":warning: Claude AI→<@{user_id}> in <#{channel_id}> "
                f"persona=`claude_ai` blockkit_invalid tier={tier} "
                f"blocks={len(card_blocks)} violations={violations[:3]}",
            )
            return "blockkit_invalid"

    feedback_blocks = build_feedback_blocks()

    # 3. Render. When a plan is present we open a single stream, animate the
    #    steps, then finalize that same message with the answer (card or prose),
    #    matching the screenshots (timeline on top, answer below).
    if plan is not None:
        mode = "plan" if plan.get("mode") == "plan" else "timeline"
        # thread_ts is bound by Bolt's say_stream context; only the display mode
        # needs to be passed through (it's set at stream-open / chat.startStream).
        streamer = await say_stream(task_display_mode=mode)
        try:
            await render_plan(streamer, plan)
        except Exception:
            # A failed animation must not strand the stream in 'in_progress' or
            # swallow the answer. Log it, then fall through to stop() so the
            # finalized message (card or prose) still lands.
            logger.exception("render_plan failed; finalizing stream with answer only")
        if card_blocks is not None:
            await streamer.stop(blocks=card_blocks + feedback_blocks)
        else:
            await streamer.stop(markdown_text=rest, blocks=feedback_blocks)
        return f"{tier}+plan:{mode}"

    # 4. No plan — original behavior, byte-for-byte.
    if card_blocks is not None:
        await say(blocks=card_blocks + feedback_blocks, text="", thread_ts=thread_ts)
    else:
        streamer = await say_stream()
        await streamer.append(markdown_text=rest)
        await streamer.stop(blocks=feedback_blocks)
    return tier
