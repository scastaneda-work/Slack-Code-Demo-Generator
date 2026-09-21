from logging import Logger

from slack_bolt.context.async_context import AsyncBoltContext
from slack_bolt.context.say.async_say import AsyncSay
from slack_bolt.context.say_stream.async_say_stream import AsyncSayStream
from slack_bolt.context.set_status.async_set_status import AsyncSetStatus
from slack_sdk.web.async_client import AsyncWebClient

from agent import AgentDeps, run_agent
from agent.audit import audit_log
from agent.identity import resolve_user_name
from agent import slackcode
from thread_context import session_store
from listeners.events.finalize import finalize_response
from listeners.events.slack_code import (
    _scenario_for,
    recognize_code_action,
    recognize_faq_edit,
    run_artifact_turn,
    run_code_action,
    run_faq_edit,
)
from listeners.events.slack_tag import run_tag_followup
from agent import tagsession
from scenarios import is_seeded

# Slack Code gate, loaded once at import. Off unless SLACK_CODE_ENABLED is set,
# so the code-channel follow-up branch is a no-op for every default bot.
_SLACK_CODE_CFG = slackcode.load_slack_code_config()


async def handle_message(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    event: dict,
    logger: Logger,
    say: AsyncSay,
    say_stream: AsyncSayStream,
    set_status: AsyncSetStatus,
):
    """Handle messages sent to the agent via DM or in threads the bot is part of."""
    # Skip message subtypes (edits, deletes, etc.) and the bot's OWN posts — but NOT
    # any bot_id (an admin/xoxp-token human post carries the Slack Admin app's bot_id,
    # a DIFFERENT id than ours; blanket-dropping any bot_id silently discards real
    # user messages — the documented admin-token trap). is_own_bot_event compares to
    # our own bot_id.
    if event.get("subtype"):
        return
    if await slackcode.is_own_bot_event(client, event):
        return

    # Capture the workspace team_id (a `T...`) for Grid team-scoped calls (see
    # app_mentioned). Cheap + idempotent; first real `T...` wins.
    slackcode.set_workspace_team_id(event.get("team") or getattr(context, "team_id", None))

    # Drop Slack's auto "Context" backlink echo. It arrives as a `message` event
    # FIRST (before the app_mention copy), so it must be caught here too — else it
    # would fall into the code-channel follow-up branch below and be answered.
    # Recognized from payload markers; scope-free, race-free; safe no-op for a
    # plain bot (which never receives these). See slackcode.is_context_echo.
    if slackcode.is_context_echo(event):
        return

    is_dm = event.get("channel_type") == "im"
    is_thread_reply = event.get("thread_ts") is not None

    if is_dm:
        pass
    elif is_thread_reply:
        # Channel thread replies are handled if the bot is already engaged — either a
        # normal SDK-session thread, OR an active Claude Tag checklist thread (which is
        # scripted and may have no SDK session). The Tag path takes priority: a human
        # follow-up there INTERRUPTS the auto-advancing checklist to re-plan.
        thr = event["thread_ts"]
        session = session_store.get_session(context.channel_id, thr)
        tag_active = (
            getattr(_SLACK_CODE_CFG, "tag_enabled", _SLACK_CODE_CFG.enabled)
            and tagsession.is_tag_thread(context.channel_id, thr)
        )
        if session is None and not tag_active:
            return
        if tag_active:
            await run_tag_followup(
                client=client, logger=logger, channel_id=context.channel_id,
                thread_ts=thr, text=event.get("text", ""), user_id=context.user_id,
            )
            return
    else:
        # Top-level channel message. Normally app_mentioned owns these — BUT a
        # code channel is a dedicated session where the spec says to "respond to
        # messages directed at you even when you aren't @-mentioned." So when
        # Slack Code is enabled and this top-level message is in a code channel,
        # treat it as a follow-up turn and answer it top-level, resuming the
        # channel-wide session. Everywhere else, app_mentioned still owns it.
        #
        # CRITICAL: Slack delivers a top-level @mention as BOTH an app_mention
        # AND a message.channels event. handle_app_mentioned already owns the
        # mention case (it replies top-level in code channels too), so here we
        # take ONLY non-mention follow-ups — otherwise one mention is answered
        # twice (two agent runs, two replies, racing on the same session key).
        text = event.get("text", "")
        bot_uid = context.bot_user_id
        is_bot_mention = bool(bot_uid) and f"<@{bot_uid}>" in text
        if (
            not is_bot_mention
            and _SLACK_CODE_CFG.enabled
            and await slackcode.is_code_channel(client, context.channel_id)
        ):
            await _reply_in_code_channel_followup(
                client=client,
                logger=logger,
                channel_id=context.channel_id,
                text=text,
                user_id=context.user_id,
            )
        return

    channel_id = context.channel_id
    text = event.get("text", "")
    thread_ts = event.get("thread_ts") or event["ts"]
    user_id = context.user_id

    try:
        existing_session_id = session_store.get_session(channel_id, thread_ts)

        await set_status(
            status="is thinking…",
            loading_messages=[
                "is reading the thread…",
                "is gathering context…",
                "is drafting a reply…",
                "is double-checking…",
            ],
        )

        deps = AgentDeps(
            client=client,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=event["ts"],
            user_token=context.user_token,
        )

        user_display_name = await resolve_user_name(client, user_id) if user_id else None

        try:
            response_text, new_session_id = await run_agent(
                text,
                session_id=existing_session_id,
                deps=deps,
                user_display_name=user_display_name,
            )
        except Exception as e:
            logger.exception("run_agent failed")
            await say(
                text="Sorry, I hit an error reaching my brain. Try again?",
                thread_ts=thread_ts,
            )
            await audit_log(
                client,
                f":warning: Claude AI→<@{user_id}> in <#{channel_id}> "
                f"persona=`claude_ai` run_agent_error={type(e).__name__}",
            )
            return

        tier = await finalize_response(
            response_text=response_text,
            client=client,
            say=say,
            say_stream=say_stream,
            logger=logger,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
        if tier in ("parse_error", "blockkit_invalid"):
            return  # finalize already replied + audited the failure

        if new_session_id:
            session_store.set_session(channel_id, thread_ts, new_session_id)

        await audit_log(
            client,
            f":speech_balloon: Claude AI→<@{user_id}> in <#{channel_id}> "
            f"persona=`claude_ai` tier={tier}",
        )

    except Exception as e:
        logger.exception(f"Failed to handle message: {e}")
        await say(
            text="Sorry, something went wrong on my end. Try again?",
            thread_ts=thread_ts,
        )
        await audit_log(
            client,
            f":warning: Claude AI→<@{user_id}> in <#{channel_id}> "
            f"persona=`claude_ai` listener_error={type(e).__name__}",
        )


async def _reply_in_code_channel_followup(
    *,
    client: AsyncWebClient,
    logger: Logger,
    channel_id: str,
    text: str,
    user_id: str | None,
) -> None:
    """Answer a NON-mention follow-up message in a code channel, per the Slack
    Code spec ("respond even when you aren't @-mentioned"). Runs the agent and
    posts TOP-LEVEL, resuming the channel-wide session keyed under thread_ts="".

    The caller (handle_message) has already dropped Context-echo events and any
    message carrying the bot's @-mention (owned by handle_app_mentioned), so this
    only ever sees a genuine non-mention follow-up.

    For a SEEDED scenario, a demo-action keyword ("run a check", "generate a
    patched version", …) short-circuits to the scripted artifact loop. For a
    FREEFORM channel those keywords are NOT scripted actions (a request like "fix
    the collision bug" is a real edit, not a /patch), so we skip the recognizer
    and let every turn flow through the live-artifact engine, which regenerates
    the file and updates the Code + Preview tabs."""
    scenario = _scenario_for(channel_id, _SLACK_CODE_CFG)
    if is_seeded(scenario):
        # The pre-baked marketer FAQ edit (near-exact phrase) short-circuits FIRST —
        # instant canned diff, not a live build, so it must not fall through to the
        # ~18s-capped agent path below.
        if recognize_faq_edit(text):
            logger.warning("code-channel FAQ edit follow-up: channel=%s", channel_id)
            await run_faq_edit(client, logger, _SLACK_CODE_CFG, channel_id)
            return
        action = recognize_code_action(text)
        if action:
            logger.warning("code-channel action follow-up: channel=%s action=%s", channel_id, action)
            await run_code_action(client, logger, _SLACK_CODE_CFG, channel_id, action)
            return

    # Live-artifact follow-up: regenerate the file, re-diff, update both tabs, and
    # stream the reply (Thinking plan block). Stop-guarded internally.
    await run_artifact_turn(
        client=client, logger=logger, cfg=_SLACK_CODE_CFG, channel_id=channel_id,
        task_text=text, first_turn=False, user_id=user_id,
    )
    await audit_log(
        client,
        f":speech_balloon: Claude AI→<@{user_id}> in code channel <#{channel_id}> "
        f"persona=`claude_ai` artifact-follow-up",
    )
