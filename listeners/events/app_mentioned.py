import re
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
    run_slack_code_session,
)
from listeners.events.slack_tag import run_tag_session, run_tag_followup
from agent import tagsession
from scenarios import is_seeded
from scenarios_tag import route_tag_scenario

# Slack Code gate, loaded once at import. Off unless SLACK_CODE_ENABLED is set,
# so this whole path is a no-op for the default bot and every other bot.
_SLACK_CODE_CFG = slackcode.load_slack_code_config()


async def handle_app_mentioned(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    event: dict,
    logger: Logger,
    say: AsyncSay,
    say_stream: AsyncSayStream,
    set_status: AsyncSetStatus,
):
    """Handle @mentions in channels."""
    # DIAG: dump every app_mention's shape the instant it arrives, before any
    # guard. Keep at WARNING until the realigned flow is confirmed once live,
    # then quiet to info.
    logger.warning(
        "app_mention IN: channel=%s ts=%s user=%s bot_id=%s subtype=%s app_id=%s text=%r",
        event.get("channel"), event.get("ts"), event.get("user"),
        event.get("bot_id"), event.get("subtype"), event.get("app_id"),
        (event.get("text") or "")[:80],
    )

    # Skip message subtypes (edits/deletes) and the bot's OWN posts — but NOT any
    # bot_id. A real teammate correcting a Tag thread via their xoxp token arrives
    # with the Slack Admin app's bot_id set (a DIFFERENT bot_id than ours); a naive
    # "drop any bot_id" silently discards that real steer (the documented admin-token
    # trap). is_own_bot_event compares to our OWN bot_id, so a human's mention lands.
    if event.get("subtype"):
        logger.warning("app_mention SKIP: subtype=%s", event.get("subtype"))
        return
    if await slackcode.is_own_bot_event(client, event):
        logger.warning("app_mention SKIP: own bot echo (bot_id=%s)", event.get("bot_id"))
        return

    channel_id = context.channel_id
    text = event.get("text", "")
    thread_ts = event.get("thread_ts") or event["ts"]
    user_id = context.user_id

    # Capture the workspace team_id (a `T...`) off the event for team-scoped calls
    # on Grid (users.list / lookupByEmail need it). The event's `team` is the
    # workspace id; context.team_id can be the enterprise `E...` on Grid, so prefer
    # the event value. Cheap + idempotent; first real `T...` wins.
    slackcode.set_workspace_team_id(event.get("team") or getattr(context, "team_id", None))

    # Drop Slack's auto "Context" backlink echo before ANY routing. When a code
    # channel is created Slack posts a backlink that quotes the origin (which
    # @-mentions the bot), re-firing as an app_mention attributed to the human.
    # Recognized from the payload markers (agent_channel_unfurl / origin blocks),
    # so it needs no channels:read and can't race. This is THE cascade guard:
    # without it the quoted "> migrate…" line trips the coding gate into spawning
    # a nested channel. Unconditional (not gated on SLACK_CODE_ENABLED) — a plain
    # bot never receives these, so it's a safe no-op there.
    if slackcode.is_context_echo(event):
        logger.warning(
            "app_mention SKIP: Context-echo backlink in channel=%s (non-actionable)",
            channel_id,
        )
        return

    try:
        cleaned_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if not cleaned_text:
            await say(
                text="Hey there! How can I help you? Ask me anything and I'll do my best.",
                thread_ts=thread_ts,
            )
            return

        # --- Slack Code routing (spec-aligned) --------------------------------
        # Two distinct cases when the feature is enabled:
        #
        # (1) The mention arrives INSIDE a code channel (recognized via
        #     is_code_channel — record_type=agent_channel). Per the Slack Code
        #     spec this is a NORMAL event — "begin work normally", replying
        #     TOP-LEVEL (no thread_ts), never creating a nested channel. (The
        #     Context-backlink echo is already dropped above.)
        #
        # (2) The mention is a coding task in a NON-code channel → spin up a
        #     dedicated code channel and run the session there, falling back to a
        #     normal thread reply if the beta can't create one.
        #
        # is_code_channel never raises (False on error), so it can't break the path.
        if _SLACK_CODE_CFG.enabled:
            in_code_channel = await slackcode.is_code_channel(client, channel_id)
            if in_code_channel:
                # In-session mention: answer top-level, resuming the channel's
                # session (keyed channel-wide under thread_ts="").
                logger.warning("app_mention GATE: channel=%s in_code_channel=True → in-session reply", channel_id)
                await _reply_in_code_channel(
                    client=client, logger=logger, channel_id=channel_id,
                    cleaned_text=cleaned_text, user_id=user_id,
                )
                return

            # A mention that is a REPLY inside an ALREADY-ACTIVE Tag thread is a
            # correction/steer, not a new task — route it to the Tag follow-up
            # (re-plan the live checklist), regardless of whether its text matches a
            # scenario keyword. This is the ONLY way a real teammate can steer a Tag
            # thread in a non-code channel: Slack delivers their message to the bot
            # only when it @-mentions Claude, and without this gate that mention would
            # fall through to a generic reply (or spin up a second Tag session). The
            # gate is scoped to a reply (thread_ts != the mention's own ts) in a known
            # Tag thread, so a fresh top-level mention still opens a new session below.
            if (
                getattr(_SLACK_CODE_CFG, "tag_enabled", _SLACK_CODE_CFG.enabled)
                and thread_ts != event["ts"]
                and tagsession.is_tag_thread(channel_id, thread_ts)
            ):
                logger.warning("app_mention GATE: channel=%s tag-followup (active thread=%s)", channel_id, thread_ts)
                await run_tag_followup(
                    client=client, logger=logger, channel_id=channel_id,
                    thread_ts=thread_ts, text=cleaned_text, user_id=user_id,
                )
                return

            # Claude Tag (lightweight, in-thread) is claimed FIRST — but only for the
            # curated phrases a Tag scenario owns (route_tag_scenario). This is what
            # lets a build-ish task like "scheduled exports" run as an in-thread
            # checklist instead of spinning up a full code channel; a genuine build
            # ("snake game") is claimed by no Tag scenario, so it falls to
            # is_coding_task below. Tag stays in THIS thread (no channel created).
            if getattr(_SLACK_CODE_CFG, "tag_enabled", _SLACK_CODE_CFG.enabled):
                tag_slug = route_tag_scenario(cleaned_text)
                if tag_slug:
                    logger.warning("app_mention GATE: channel=%s tag=%s → in-thread checklist", channel_id, tag_slug)
                    handled = await run_tag_session(
                        client=client, logger=logger, channel_id=channel_id,
                        thread_ts=thread_ts, slug=tag_slug, user_id=user_id,
                    )
                    if handled:
                        return
                    # else (scenario failed to load) fall through

            if slackcode.is_coding_task(cleaned_text, _SLACK_CODE_CFG):
                logger.warning("app_mention GATE: channel=%s coding=True in_code_channel=False → create-session", channel_id)
                handled = await run_slack_code_session(
                    client=client,
                    logger=logger,
                    cfg=_SLACK_CODE_CFG,
                    task_text=cleaned_text,
                    origin_channel_id=channel_id,
                    origin_thread_ts=thread_ts,
                    origin_message_ts=event["ts"],
                    user_id=user_id,
                )
                if handled:
                    return
                # else fall through to the normal in-thread reply below

        await set_status(
            status="is thinking…",
            loading_messages=[
                "is reading the thread…",
                "is gathering context…",
                "is drafting a reply…",
                "is double-checking…",
            ],
        )

        existing_session_id = session_store.get_session(channel_id, thread_ts)

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
                cleaned_text,
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
        logger.exception(f"Failed to handle app mention: {e}")
        await say(
            text="Sorry, something went wrong on my end. Try again?",
            thread_ts=thread_ts,
        )
        await audit_log(
            client,
            f":warning: Claude AI→<@{user_id}> in <#{channel_id}> "
            f"persona=`claude_ai` listener_error={type(e).__name__}",
        )


async def _reply_in_code_channel(
    *,
    client: AsyncWebClient,
    logger: Logger,
    channel_id: str,
    cleaned_text: str,
    user_id: str | None,
) -> None:
    """Answer an @mention that arrived INSIDE a code channel, per the Slack Code
    spec ("begin work normally"): run the agent and post TOP-LEVEL (no thread_ts),
    resuming the channel's session (keyed channel-wide under thread_ts="").

    Bolt's say/say_stream can't post top-level in a code channel, so we run the
    agent directly and post via chat.postMessage through the shared top-level
    helper. Best-effort status flips bracket the work; all failures surface in
    the channel rather than the origin thread.

    For a SEEDED scenario, a demo-action keyword ("run a check", "generate a
    patched version", "make a recap", "show the dashboard") short-circuits to the
    scripted, deterministic artifact loop — the flashy in-mention path for the
    verify/roll-up beats. For a FREEFORM channel those keywords aren't scripted
    actions, so every mention flows through the live-artifact engine (regenerate
    the file → update Code + Preview → stream the reply).
    """
    scenario = _scenario_for(channel_id, _SLACK_CODE_CFG)
    if is_seeded(scenario):
        # The pre-baked marketer FAQ edit (near-exact phrase) short-circuits FIRST —
        # it's an instant canned diff, not a live build, so it must not fall through
        # to the ~18s-capped agent path below.
        if recognize_faq_edit(cleaned_text):
            logger.warning("code-channel FAQ edit mention: channel=%s", channel_id)
            await run_faq_edit(client, logger, _SLACK_CODE_CFG, channel_id)
            return
        action = recognize_code_action(cleaned_text)
        if action:
            logger.warning("code-channel action mention: channel=%s action=%s", channel_id, action)
            await run_code_action(client, logger, _SLACK_CODE_CFG, channel_id, action)
            return

    await run_artifact_turn(
        client=client, logger=logger, cfg=_SLACK_CODE_CFG, channel_id=channel_id,
        task_text=cleaned_text, first_turn=False, user_id=user_id,
    )
    await audit_log(
        client,
        f":speech_balloon: Claude AI→<@{user_id}> in code channel <#{channel_id}> "
        f"persona=`claude_ai` artifact-in-session-reply",
    )
