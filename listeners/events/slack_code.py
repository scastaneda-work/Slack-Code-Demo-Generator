"""Slack Code working-session orchestration for the Claude AI bot.

When an @mention is a coding task (per the keyword gate in
``agent.slackcode``), this module spins up a dedicated code channel and runs the
session there instead of replying in the origin thread:

    create channel -> status(processing) -> context bar -> run the agent and
    post its reply top-level -> publish a diff view -> post a summary ->
    status(active)

Everything is gated behind ``SLACK_CODE_ENABLED`` and degrades gracefully: if
the Slack Code beta isn't enabled for this app (``feature_disabled``), or the
channel can't be created, the caller falls back to a normal thread reply. The
diff / PR / CI values are FAKE (this bot has no repo/GitHub) — convincing demo
props, not real artifacts.

This is a per-bot listener (not shared): Slack Code is a Claude-specific demo
surface for now.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from logging import Logger

from slack_sdk.web.async_client import AsyncWebClient

from agent import AgentDeps
from agent.agent import run_agent_offloop  # code-channel turns run off the Socket Mode loop
from agent.audit import audit_log
from agent.identity import resolve_user_name
from agent import slackcode
from agent.taskplan import extract_plan, validate_plan
from agent.blockkit import extract_blocks, validate_blocks
from agent.htmlblock import extract_html
from agent.artifact_diff import unified as unified_diff
from agent.render import (
    render_plan,
    render_plan_steps_complete,
    render_plan_steps_pending,
)
from scenarios import load_scenario, is_seeded, route_scenario
from thread_context import session_store


def _scenario_for(channel_id: str, cfg: slackcode.SlackCodeConfig):
    """Resolve the scenario for a code channel: the story it was OPENED with
    (recorded at creation by route_scenario), falling back to the env default
    (cfg.scenario) for a channel we have no record of — e.g. one created before
    this build, or after a restart. This is what makes one running agent serve
    all three stories, each channel locked to its own."""
    slug = slackcode.get_channel_scenario(channel_id) or cfg.scenario
    return load_scenario(slug)


async def _ensure_member(client: AsyncWebClient, logger: Logger, channel_id: str) -> bool:
    """Best-effort: make sure the bot is a member of `channel_id`, joining once.

    The bot is auto-invited to channels IT creates, so this is a safety net for
    the added-to-an-existing-channel path and the odd case where a stream/post
    returns `not_in_channel`. conversations.join works on public channels (needs
    channels:join); a private channel can't be self-joined, so that failure is
    swallowed (the caller degrades). Returns True if we're (now) plausibly a
    member. Never raises."""
    try:
        await client.conversations_join(channel=channel_id)
        return True
    except Exception as e:  # noqa: BLE001 — private channel / already-in / transient: degrade
        logger.info("conversations.join(%s) did not succeed (%s) — continuing", channel_id, type(e).__name__)
        return False


def _repo_facts(cfg: slackcode.SlackCodeConfig, scenario=None) -> tuple[str, str, str]:
    """Resolve (repo, repo_url, pr_url) for the active session, preferring the
    scenario's own identity so each story shows its OWN repo/PR (checkout-service
    #914, web-app #2287, users-service #1043, …) rather than the shared config
    defaults. Falls back to cfg for freeform (which declares a neutral sandbox with
    no PR) or when no scenario is resolved."""
    repo = getattr(scenario, "REPO", None) or cfg.fake_repo
    repo_url = getattr(scenario, "REPO_URL", None) or cfg.fake_repo_url
    pr_url = getattr(scenario, "PR_URL", None) or cfg.fake_pr_url
    return repo, repo_url, pr_url


def _pr_label(pr_url: str) -> str:
    """A context-bar PR chip label from a PR url, e.g. '.../pull/914' → 'PR #914
    (draft)'. Falls back to a generic label when the url has no number."""
    import re as _re
    m = _re.search(r"/pull/(\d+)", pr_url or "")
    return f"PR #{m.group(1)} (draft)" if m else "PR (draft)"


def _build_context_bar(
    cfg: slackcode.SlackCodeConfig, *, branch: str, phase: str = "open",
    check_label: str = "durability", scenario=None,
) -> list[dict]:
    """The fake-but-plausible context bar — the engineer's orientation across the
    top of the channel, which PROGRESSES through the story's arc (the spec's
    "update them as the work progresses / keep the labels current"). Every
    setProperties call REPLACES the whole set, so this returns the full bar for a
    given `phase`; we deliberately add/remove chips to track where the work
    stands rather than hold a fixed count.

    Most chips are LINK chips (info + a url) pointing at the ACTIVE SCENARIO's own
    fake-but-plausible repo/PR/CI pages (see _repo_facts), so a click reads as a
    real shortcut and the labels match the story (acme/checkout-service #914, …).

    We ALSO publish one item_type:"action" chip ("Create PR") from the "working"
    phase on. The properties doc (beta) says an action chip's click delivers a
    code_channel_action to the app, and we have a handler wired for it
    (listeners/events/code_channel_action.py, mapped: create-pr → the canned PR
    post). BUT a live test (2026-09-17, Socket Mode) confirmed the click currently
    fires NOTHING — no code_channel_action, no Bolt "unhandled request", just
    silence; link (url) chips DO open their url. So this chip is presently INERT —
    kept DELIBERATELY as a forward bet: the handler + chip are staged so that if a
    later beta build starts delivering code_channel_action, it lights up with no
    code change. (Slack also has no deep link that runs a slash command or prefills
    the composer — docs.slack.dev/interactivity/deep-linking — so a url chip can't
    stand in for the action either.) The WORKING triggers today are the slash
    commands (/check-<label>, /patch, /show-metrics, /make-recap) and @-mention
    keywords. Do NOT delete this chip as "dead code" — its inertness is expected
    and its presence is intentional pending the beta.

    Phases (chip set grows/settles with the beat; ≤5 items, the doc's hard cap):
      • "open"     — agent spinning up: repo · branch (orientation only)
      • "working"  — a diff/preview is up: repo · branch · PR (draft) · [Create PR]
      • "checking" — a check running/failed: repo · branch · PR · CI: failing · [Create PR]
      • "passed"   — patch applied, green:   repo · branch · PR · check: pass · [Create PR]
    Unknown phase falls back to "working"."""
    repo, repo_url, pr_url = _repo_facts(cfg, scenario)
    items = [
        {"key": "repo", "label": repo, "icon": "folder", "url": repo_url},
        {"key": "branch", "label": branch, "icon": "branch",
         "url": f"{repo_url}/tree/{branch}"},
    ]
    if phase == "open":
        return items
    # From "working" on, there's a reviewable PR.
    items.append({"key": "pr", "label": _pr_label(pr_url), "icon": "hierarchy", "url": pr_url})
    if phase == "checking":
        # CI reflects the failing check — the "caught before customers" beat.
        items.append({"key": "ci", "label": "CI: failing", "icon": "terminal",
                      "url": f"{repo_url}/actions"})
    elif phase == "passed":
        # After the patch: the check gate is green. 'lock' reads as a passed gate.
        items.append({"key": "check", "label": f"{check_label}: pass", "icon": "lock",
                      "url": f"{repo_url}/actions"})
    # An interactive chip (key="create-pr"). Per the beta doc its click should
    # deliver a code_channel_action that our handler turns into the canned draft-PR
    # message — but as of 2026-09-17 the click fires nothing (see the docstring);
    # kept as a forward bet for when the beta delivers it. Last, within the ≤5 cap
    # (working=4, checking/passed=5). NO url — a url makes it a link chip instead.
    items.append({"key": "create-pr", "label": "Create PR", "icon": "hierarchy",
                  "item_type": "action"})
    return items


async def _fetch_origin_context(
    client: AsyncWebClient,
    logger: Logger,
    *,
    origin_channel_id: str,
    origin_thread_ts: str,
    origin_message_ts: str,
    limit: int = 15,
) -> str:
    """Return a compact plaintext transcript of the origin conversation.

    The code channel is brand-new and empty, and Slack does NOT pass the origin
    thread's history into the session (agents.conversations.create only records a
    back-reference link). So we fetch it ourselves and hand it to the agent as
    context — otherwise the model gets a bare coding imperative with no idea which
    repo/cron/target the channel was discussing, and falls out of persona asking
    "where does this live?".

    Best-effort: any failure returns "" so a history hiccup never blocks the
    session. Skips the triggering mention itself and bot messages.
    """
    try:
        # Thread reply → pull the thread; top-level mention → pull channel history.
        if origin_thread_ts and origin_thread_ts != origin_message_ts:
            resp = await client.conversations_replies(
                channel=origin_channel_id, ts=origin_thread_ts, limit=limit
            )
        else:
            resp = await client.conversations_history(
                channel=origin_channel_id, limit=limit
            )
        messages = list(resp.get("messages", []))
    except Exception:
        logger.warning("origin-context fetch failed; proceeding without it", exc_info=True)
        return ""

    # conversations_history is newest-first; replies is oldest-first. Normalize
    # to chronological (oldest-first) for a readable transcript.
    messages.sort(key=lambda m: float(m.get("ts", "0") or 0))

    lines: list[str] = []
    for m in messages:
        if m.get("subtype") or m.get("bot_id"):
            continue  # skip system/bot messages (incl. our own mention target)
        if m.get("ts") == origin_message_ts:
            continue  # skip the triggering @mention itself
        body = (m.get("text") or "").strip()
        if not body:
            continue
        uid = m.get("user")
        name = None
        if uid:
            try:
                name = await resolve_user_name(client, uid)
            except Exception:
                name = None
        speaker = name or (uid or "someone")
        # Truncate any single message so a long paste can't blow up the prompt.
        if len(body) > 600:
            body = body[:600] + "…"
        lines.append(f"{speaker}: {body}")

    return "\n".join(lines[-limit:])


# ---------------------------------------------------------------------------
# Artifact publishing (scenario-driven)
# ---------------------------------------------------------------------------
# Every artifact's content comes from the active scenario module (scenarios/…),
# so the diff, preview, dashboard, and recap can never disagree. Each tab is one
# set_view call keyed by a distinct view_key so they coexist. Publishing helpers
# are shared by the session orchestrator AND the slash-command / mention patch
# handlers, so the fail→patch→pass loop updates the exact same tabs.


def _scenario_preview_html(scenario, *, patched: bool, faq: bool = False) -> str:
    """Call ``scenario.preview_html`` with the FAQ variant when requested, tolerating
    scenarios whose signature predates the keyword-only ``faq`` param (they just get
    the normal page). Keeps the FAQ opt-in additive and scenario-agnostic."""
    if faq:
        try:
            return scenario.preview_html(patched, faq=True)
        except TypeError:
            pass  # scenario has no `faq` param — fall back to the normal page
    return scenario.preview_html(patched)


async def _publish_preview(
    client: AsyncWebClient, channel_id: str, scenario, *, patched: bool, faq: bool = False,
) -> slackcode.SlackCodeResult:
    """Publish/refresh the Preview tab (the 'what it looks like' HTML view).

    The beta's html view can reject a payload (`view_creation_failed`) for reasons
    that aren't documented — size, `<!doctype>`/`<head>` wrappers, entities, etc.
    (A tiny `<h1>` probe succeeds; the full 3KB styled page did not.) So we don't
    send one shape and hope: we try the full page first, then progressively
    simpler fallbacks, and return the first that lands — a degraded-but-present
    Preview beats a missing tab. Each attempt is logged so the failing shape is
    visible.

    ``faq`` (website_redesign only) adds the pre-baked expandable FAQ section —
    passed through to ``preview_html``; scenarios without an FAQ ignore it."""
    import logging as _logging
    log = _logging.getLogger("claude-ai-bot")

    full = _scenario_preview_html(scenario, patched=patched, faq=faq)
    attempts: list[tuple[str, str]] = [("full", full)]
    # Fallback 1: strip the doctype/html/head wrapper — send just the body's
    # inner markup + an inline <style> (some HTML sanitizers reject full docs).
    if "<body>" in full and "</body>" in full:
        inner = full.split("<body>", 1)[1].rsplit("</body>", 1)[0]
        style = ""
        if "<style>" in full and "</style>" in full:
            style = "<style>" + full.split("<style>", 1)[1].split("</style>", 1)[0] + "</style>"
        attempts.append(("body-only", style + inner))
    # Fallback 2: a minimal styled page (proves the view works at all).
    attempts.append((
        "minimal",
        "<div style=\"font-family:sans-serif;padding:24px\">"
        "<h1 style=\"color:#14203A\">WelloGuard</h1>"
        "<p>Homepage redesign preview.</p></div>",
    ))

    result = slackcode.SlackCodeResult(ok=False, error="no attempt made")
    trace: list[str] = []
    for shape, content in attempts:
        # Bound each attempt: a beta html setView that STALLS (rather than erroring)
        # would otherwise freeze the whole session on "processing" — which is
        # exactly what a large page appears to do. On timeout, record it and move
        # on to the next (smaller) shape instead of hanging.
        try:
            r = await asyncio.wait_for(
                slackcode.set_view(
                    client, channel_id,
                    view_type="html", view_key="preview", name="Preview", content=content,
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            r = slackcode.SlackCodeResult(ok=False, error="timeout(15s)")
        result = r
        log.warning("preview html attempt shape=%s len=%d -> ok=%s error=%s",
                    shape, len(content), r.ok, r.error)
        trace.append(f"{shape}(len={len(content)}) → {'OK' if r.ok else '`' + str(r.error) + '`'}")
        if r.ok:
            if shape != "full":
                log.warning("preview fell back to shape=%s (full page rejected)", shape)
            break
    # When debugging, post the full attempt trace INTO the channel — slack run
    # swallows stdout logging and the kit bot's audit_log isn't landing, so this
    # is the only place the result is visible. Always posts (success or fail).
    import os
    if (os.environ.get("SLACK_CODE_VIEW_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            await client.chat_postMessage(
                channel=channel_id,
                markdown_text=":microscope: _preview-debug_ — html setView attempts:\n• "
                + "\n• ".join(trace),
            )
        except Exception:
            log.warning("preview-debug post failed", exc_info=True)
    return result


async def _publish_dashboard(client: AsyncWebClient, channel_id: str, scenario) -> slackcode.SlackCodeResult | None:
    """Publish the Dashboard tab (Block Kit KPI rows). block_kit views take the
    blocks as a real JSON ARRAY (NOT a json.dumps'd string — confirmed via
    /view-probe: only the `blocks(array)` variant returned OK). Validated first; a
    bad card is skipped rather than sent (validate_blocks returns [] when clean)."""
    blocks = scenario.dashboard_blocks()
    if validate_blocks(blocks):
        return None  # malformed — skip the tab rather than post a broken view
    return await slackcode.set_view(
        client, channel_id,
        view_type="block_kit", view_key="dashboard", name="Dashboard",
        blocks=blocks,
    )


async def _publish_diff(
    client: AsyncWebClient, channel_id: str, scenario, *, branch: str, patched: bool, faq: bool = False,
) -> slackcode.SlackCodeResult:
    """Publish/refresh the Code tab (diff view). Same base/head each time so a later
    diff upserts in place instead of stacking a second diff tab.

    ``faq`` (website_redesign only) publishes the pre-baked FAQ-accordion diff
    instead of base/patch — the marketer's instant edit."""
    if faq and hasattr(scenario, "faq_diff"):
        diff = scenario.faq_diff()
    else:
        diff = scenario.patch_diff() if patched else scenario.base_diff()
    return await slackcode.set_view(
        client, channel_id,
        view_type="diff", content=diff, base_branch="main", head_branch=branch,
    )


async def _report_view_results(client, logger, channel_id, results) -> None:
    """Log + audit the outcome of every artifact-view publish so the audit channel
    (#claude-demo-changes-log) carries a durable per-run record of which tabs
    landed and which didn't — the reliable trail (slack run swallows stdout logs,
    so this is where to look). Posts ONE audit line per run.

    `results` is a list of (label, value); value is a SlackCodeResult, None
    (skipped), or an Exception (from gather return_exceptions)."""
    landed: list[str] = []
    failures: list[str] = []
    for label, val in results:
        if val is None:
            logger.info("view %s skipped (nothing to publish)", label)
            continue
        if isinstance(val, Exception):
            logger.warning("view %s raised %s", label, type(val).__name__)
            failures.append(f"{label}=`{type(val).__name__}`")
            continue
        ok = getattr(val, "ok", False)
        err = getattr(val, "error", None)
        logger.warning("view %s -> ok=%s error=%s", label, ok, err)
        if ok:
            landed.append(label)
        else:
            failures.append(f"{label}=`{err}`")

    # One durable audit line per run: what published, and anything that didn't.
    parts = []
    if landed:
        parts.append(f"published {', '.join(landed)}")
    if failures:
        parts.append(f":warning: FAILED {', '.join(failures)}")
    if parts:
        icon = ":framed_picture:" if not failures else ":rotating_light:"
        await audit_log(
            client,
            f"{icon} Claude AI Slack Code artifacts in <#{channel_id}> — {'; '.join(parts)}.",
        )

    # Surface failures IN the code channel too, but ONLY when debugging — a
    # transient hiccup shouldn't print a warning line mid-demo. SLACK_CODE_VIEW_DEBUG=1.
    if failures:
        import os
        if (os.environ.get("SLACK_CODE_VIEW_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                await client.chat_postMessage(
                    channel=channel_id,
                    markdown_text=f":warning: _diagnostic_ — view tabs that didn't land: {', '.join(failures)}",
                )
            except Exception:
                logger.warning("view-failure channel post failed", exc_info=True)


def _join_phrases(parts: list[str]) -> str:
    """Join phrases into 'a, b, and c' (Oxford-style). Used for the scenario-aware
    'check out the artifacts — …' summary so it names only the tabs that exist."""
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _branch_prefix(cfg: slackcode.SlackCodeConfig, scenario=None) -> str:
    """The branch prefix for the active session, preferring the scenario's own
    (e.g. checkout_incident → 'hotfix/', billing_webhook → 'feat/') over the
    config default. A scenario that declares an empty BRANCH_PREFIX (freeform,
    which builds on master) falls back to the cfg default — harmless there since
    the orchestrator forces 'master' for freeform anyway."""
    return getattr(scenario, "BRANCH_PREFIX", None) or cfg.fake_branch_prefix


def _branch_for(cfg: slackcode.SlackCodeConfig, channel_name: str, scenario=None) -> str:
    """Reconstruct the session branch from the code channel's name. The channel
    was named channel_name_for(task) = channel_prefix + slug; the branch is the
    scenario's branch prefix + slug. Used by the action helpers (which only have
    the channel, not the original task_text)."""
    slug = (channel_name or "").removeprefix(cfg.channel_name_prefix)
    prefix = _branch_prefix(cfg, scenario)
    return f"{prefix}{slug}"[:60] or f"{prefix}session"


async def _channel_name(client: AsyncWebClient, channel_id: str) -> str:
    """Best-effort code-channel name (for reconstructing the branch). "" on error.

    Passes the bot's team_id (like is_code_channel) so the call survives an
    Enterprise Grid org that requires it; harmless elsewhere, omitted when unknown.
    """
    try:
        kwargs = {"channel": channel_id, "include_num_members": False}
        ident = await slackcode.get_bot_identity(client)
        if ident.get("team_id"):
            kwargs["team_id"] = ident["team_id"]
        resp = await client.conversations_info(**kwargs)
        return (resp.get("channel") or {}).get("name", "") or ""
    except Exception:  # noqa: BLE001 — name is cosmetic; never break the action
        return ""


# --- Shared action helpers: the check → patch → re-check loop, recap, metrics -
# Both the runtime slash commands (listeners/commands) and the in-channel mention
# keyword branches (app_mentioned / message) call these, so the beat behaves
# identically however it's triggered. Every helper is scripted + deterministic
# and posts the authoritative verdict as a top-level chat.postMessage (always
# renders, independent of view/beta state); the view updates are enhancement.
#
# These scripted beats belong to the SEEDED stories (website_redesign /
# billing_webhook). In a freeform channel there's no scripted check/patch/metrics/
# recap, and the scenario module doesn't even define check_*/dashboard/recap — so
# each helper guards on is_seeded and no-ops with a friendly note. (The mention
# path already skips recognize_code_action for freeform; this guard also covers a
# stray manually-typed slash command like /check-contrast in a freeform channel.)


async def _no_scripted_action(client: AsyncWebClient, channel_id: str) -> None:
    """Politely decline a scripted-demo action in a freeform channel (no crash)."""
    try:
        await client.chat_postMessage(
            channel=channel_id,
            markdown_text=(
                "That's one of the guided-demo commands (check / patch / metrics / recap) — "
                "this channel is a freeform build, so there's no scripted check to run. "
                "Just tell me what to change and I'll update the file."
            ),
        )
    except Exception:  # noqa: BLE001 — best-effort; never raise from a guard
        pass


# --- Human participants: scripted chime-ins that show humans working WITH Claude -
# 1–2 people from the origin thread (scenario.PARTICIPANTS) post short scripted
# lines in the code channel. Two phases: "arrival" (as the channel opens — the
# stall beat while Claude's first reply loads) and "on_artifacts" (just after the
# diff/dashboard publish — reacting to what Claude shipped). Posted via
# chat:write.customize (spoofed username); best-effort + stop-guarded. Only for
# SEEDED stories with authored beats — freeform has no origin cast, so this no-ops.


def _participant_email_stems(scenario) -> list[str]:
    """The stable persona stems to (optionally) invite for this scenario's cast."""
    parts = getattr(scenario, "PARTICIPANTS", None) or []
    return [p["email_stem"] for p in parts if isinstance(p, dict) and p.get("email_stem")]


async def _post_participant_beats(
    client: AsyncWebClient, logger: Logger, channel_id: str, scenario, phase: str,
) -> None:
    """Post the scenario's scripted human chime-ins for `phase` (spoofed usernames).
    No-op for freeform / a scenario without beats, and stop-guarded per beat. Fully
    best-effort — a failed chime-in never blocks the session.

    Slack-ification (sparing): on the ARRIVAL beat, Claude adds a single :eyes:
    reaction to the first human line — the agent visibly noticing its teammate.
    Just one reaction; we deliberately don't react to every message."""
    if not is_seeded(scenario):
        return
    getter = getattr(scenario, "participant_beats", None)
    if not callable(getter):
        return
    try:
        beats = getter() or []
    except Exception:  # noqa: BLE001 — bad beat data must never break the session
        logger.warning("participant_beats() raised for %s — skipping", channel_id, exc_info=True)
        return
    # Map beat name → the participant's email_stem, so we can fetch each persona's
    # real avatar (icon_url) for the spoofed post. A beat name with no matching
    # PARTICIPANT still posts (just with the fallback icon).
    parts = getattr(scenario, "PARTICIPANTS", None) or []
    stem_by_name = {p.get("name"): p.get("email_stem") for p in parts if isinstance(p, dict)}
    reacted = False
    for beat in beats:
        if not isinstance(beat, dict) or beat.get("phase") != phase:
            continue
        if slackcode.is_session_stopped(channel_id):
            return
        name = beat.get("name")
        text = beat.get("text")
        if not name or not text:
            continue
        # Resolve the persona's real avatar so the chime-in shows their photo, not
        # the bot's. Best-effort — falls back to a neutral icon inside post_as_participant.
        icon_url = None
        stem = stem_by_name.get(name)
        if stem:
            info = await slackcode.resolve_participant(client, stem)
            if info:
                icon_url = info.get("icon_url")
        r = await slackcode.post_as_participant(client, channel_id, name=name, text=text, icon_url=icon_url)
        # One light Claude reaction: acknowledge the FIRST arrival chime-in with :eyes:.
        if phase == "arrival" and not reacted and getattr(r, "ok", False):
            ts = (getattr(r, "data", None) or {}).get("ts")
            if ts and not slackcode.is_session_stopped(channel_id):
                await slackcode.react(client, channel_id, ts, "eyes")
                reacted = True


async def run_durability_check(
    client: AsyncWebClient, logger: Logger, cfg: slackcode.SlackCodeConfig, channel_id: str,
) -> None:
    """Run the scenario's check. FAIL until the channel is patched, PASS after —
    the demo's verify beat. Deterministic (the agent never computes it)."""
    scenario = _scenario_for(channel_id, cfg)
    if not is_seeded(scenario):
        await _no_scripted_action(client, channel_id)
        return
    passed = slackcode.is_channel_patched(channel_id)
    report = scenario.check_pass_report() if passed else scenario.check_fail_report()

    # Update the context bar to track the check state as part of the beat: a FAIL
    # flips CI to "failing" (the "caught before customers" moment); a PASS (re-check
    # after patch) shows the green check gate. The bar reflects where the work stands.
    async def _update_bar_for_check() -> None:
        branch = _branch_for(cfg, await _channel_name(client, channel_id), scenario)
        await slackcode.set_context_bar(
            client, channel_id,
            _build_context_bar(
                cfg, branch=branch, phase=("passed" if passed else "checking"),
                check_label=scenario.CHECK_LABEL, scenario=scenario,
            ),
        )

    # Thinking pulse → (update bar) → verdict, as one streamed message.
    await run_scripted_beat(
        client=client, logger=logger, channel_id=channel_id, scenario=scenario,
        action="check", work=_update_bar_for_check, verdict_markdown=report,
    )
    await audit_log(
        client,
        f":mag: Claude AI Slack Code check ({scenario.CHECK_LABEL}) in <#{channel_id}> → "
        f"{'PASS' if passed else 'FAIL'}.",
    )


async def run_patch(
    client: AsyncWebClient, logger: Logger, cfg: slackcode.SlackCodeConfig, channel_id: str,
) -> None:
    """Apply the scripted patch: mark the channel patched, refresh the Code +
    Preview tabs to the fixed state, add the check-pass item to the context bar,
    post the clean-pass verdict. Idempotent — re-running just re-asserts PASS."""
    scenario = _scenario_for(channel_id, cfg)
    if not is_seeded(scenario):
        await _no_scripted_action(client, channel_id)
        return
    slackcode.mark_channel_patched(channel_id)
    branch = _branch_for(cfg, await _channel_name(client, channel_id), scenario)

    artifacts = getattr(scenario, "ARTIFACTS", {"diff", "preview", "dashboard"})
    await slackcode.set_session_status(client, channel_id, "processing")
    # Wrap the working body so status ALWAYS returns to active on exit (unless the
    # session was stopped — then Slack owns it). Prevents a stranded "processing"
    # spinner if any setView / post between here and the end throws.
    try:
        # Refresh the visible artifacts to the patched state SEQUENTIALLY (concurrent
        # setView calls race on the channel's view array), then post the verdict —
        # all bracketed by the Thinking pulse so the beat reads "Thinking → fixed".
        async def _do_patch_work() -> None:
            await _publish_diff(client, channel_id, scenario, branch=branch, patched=True)
            if "preview" in artifacts:
                await _publish_preview(client, channel_id, scenario, patched=True)
                # Keep the live-artifact store in sync with the scripted patch, so a
                # later freeform edit ("now make the hero blue") builds on the PATCHED
                # page rather than reverting to the pre-patch seed.
                try:
                    if hasattr(scenario, "seed_filename"):
                        slackcode.set_channel_artifact(
                            channel_id, filename=scenario.seed_filename(),
                            html=scenario.preview_html(True),
                        )
                except Exception:  # noqa: BLE001 — store sync is best-effort
                    logger.warning("patch: artifact store sync failed", exc_info=True)
            await slackcode.set_context_bar(
                client, channel_id,
                _build_context_bar(
                    cfg, branch=branch, phase="passed",
                    check_label=scenario.CHECK_LABEL, scenario=scenario,
                ),
            )

        tabs = "*Code* and *Preview*" if "preview" in artifacts else "the *Code* diff"
        verdict = (
            f":hammer_and_wrench: Applied the patch and re-ran the check — {tabs} "
            "now show the fixed version.\n\n"
            + scenario.check_pass_report()
        )
        await run_scripted_beat(
            client=client, logger=logger, channel_id=channel_id, scenario=scenario,
            action="patch", work=_do_patch_work, verdict_markdown=verdict,
        )
    finally:
        if not slackcode.is_session_stopped(channel_id):
            await slackcode.set_session_status(client, channel_id, "active")
    await audit_log(
        client,
        f":hammer_and_wrench: Claude AI Slack Code patch applied in <#{channel_id}> — "
        f"{scenario.CHECK_LABEL} check now PASSES.",
    )


async def run_faq_edit(
    client: AsyncWebClient, logger: Logger, cfg: slackcode.SlackCodeConfig, channel_id: str,
) -> None:
    """Serve the pre-baked "Jennifer edit" INSTANTLY: a marketer asked for an
    expandable FAQ, so we republish the Code diff + Preview page with a CSS-only
    <details>/<summary> accordion — no agent turn — inside a short Thinking pulse.

    website_redesign only (the sole scenario with faq_diff / an FAQ preview); a
    friendly no-op elsewhere. The edit COMPOSES with the current contrast state:
    we pass the channel's real `patched` flag so a prior /patch isn't reverted, and
    an unpatched page keeps its (failing) CTA — the FAQ doesn't silently fix
    contrast. The live-artifact store is synced so a later freeform edit builds on
    the FAQ page. No participant beat fires (run_scripted_beat posts only the
    Thinking pulse + verdict) — Claude's response only, as specced."""
    scenario = _scenario_for(channel_id, cfg)
    if getattr(scenario, "SLUG", None) != "website_redesign" or not hasattr(scenario, "faq_diff"):
        await _no_scripted_action(client, channel_id)
        return
    branch = _branch_for(cfg, await _channel_name(client, channel_id), scenario)
    patched = slackcode.is_channel_patched(channel_id)  # keep the CTA-contrast state

    await slackcode.set_session_status(client, channel_id, "processing")
    try:
        # Refresh Code + Preview to the FAQ state SEQUENTIALLY (concurrent setView
        # calls race on the channel's single view array — the "only 1 artifact" bug),
        # bracketed by the Thinking pulse so the beat reads "Thinking → shipped".
        async def _do_faq_work() -> None:
            await _publish_diff(client, channel_id, scenario, branch=branch, patched=patched, faq=True)
            await _publish_preview(client, channel_id, scenario, patched=patched, faq=True)
            # Keep the live-artifact store in sync so a later freeform edit builds
            # on the FAQ page rather than reverting to the pre-FAQ version.
            try:
                if hasattr(scenario, "seed_filename"):
                    slackcode.set_channel_artifact(
                        channel_id, filename=scenario.seed_filename(),
                        html=_scenario_preview_html(scenario, patched=patched, faq=True),
                    )
            except Exception:  # noqa: BLE001 — store sync is best-effort
                logger.warning("faq edit: artifact store sync failed", exc_info=True)

        verdict = (
            ":sparkles: Added an expandable FAQ to the page — pure CSS "
            "(`<details>`/`<summary>`, no JavaScript). The *Code* diff and the "
            "*Preview* now show it; click a question in the Preview to expand it."
        )
        # Reuse the "patch" dwell — thematically "reworking the page", and it keeps
        # the beat's timing in the same family as /patch (an instant pre-built edit).
        await run_scripted_beat(
            client=client, logger=logger, channel_id=channel_id, scenario=scenario,
            action="patch", work=_do_faq_work, verdict_markdown=verdict,
        )
    finally:
        if not slackcode.is_session_stopped(channel_id):
            await slackcode.set_session_status(client, channel_id, "active")
    await audit_log(
        client,
        f":sparkles: Claude AI Slack Code FAQ edit served in <#{channel_id}> "
        "(pre-baked, no agent turn).",
    )


async def run_show_metrics(
    client: AsyncWebClient, logger: Logger, cfg: slackcode.SlackCodeConfig, channel_id: str,
) -> None:
    """(Re)publish the Dashboard tab and point the user at it."""
    scenario = _scenario_for(channel_id, cfg)
    if not is_seeded(scenario):
        await _no_scripted_action(client, channel_id)
        return
    async def _do_metrics_work() -> None:
        await _publish_dashboard(client, channel_id, scenario)

    await run_scripted_beat(
        client=client, logger=logger, channel_id=channel_id, scenario=scenario,
        action="metrics", work=_do_metrics_work,
        verdict_markdown=(
            ":bar_chart: The reliability numbers are in the *Dashboard* tab — "
            "before vs after the refactor. Scoped to this channel's members."
        ),
    )


async def run_make_recap(
    client: AsyncWebClient, logger: Logger, cfg: slackcode.SlackCodeConfig, channel_id: str,
) -> None:
    """Publish the 5-part leadership recap as a living CANVAS tab (the confirmed
    two-step flow: canvases.create → setView type=canvas by canvas_id). On a
    follow-up ("update the recap") it EDITS the same canvas in place
    (canvases.edit) so the tab + any comments persist. Falls back to a formatted
    message if the canvas path fails (beta gated / scope missing / any error) so
    the 'turn this into 5 slides' beat always lands. Recap content comes from the
    same scenario fact set as the Dashboard, so the numbers always agree."""
    scenario = _scenario_for(channel_id, cfg)
    if not is_seeded(scenario):
        await _no_scripted_action(client, channel_id)
        return

    markdown = scenario.recap_canvas()  # canvas-flavored markdown (## sections)
    existing = slackcode.get_recap_canvas_id(channel_id)

    # The canvas work runs inside the Thinking pulse; it records the pointer text
    # (differs for edit-in-place vs first-publish) which the pulse then finalizes
    # with — so the beat reads "Thinking: writing the recap… → here's the tab".
    outcome: dict[str, str | None] = {"verdict": None, "audit": None}

    async def _do_recap_work() -> None:
        try:
            if existing:
                r = await slackcode.set_canvas_content(client, canvas_id=existing, markdown=markdown)
                if r.ok:
                    outcome["verdict"] = ":memo: Updated the *Leadership recap* canvas tab."
                    outcome["audit"] = f":memo: Claude AI Slack Code recap canvas updated in <#{channel_id}>."
                    return
                logger.warning("recap canvas edit failed (%s) — re-publishing", r.error)
            r = await slackcode.publish_canvas_view(
                client, channel_id, title="Leadership recap", markdown=markdown,
                view_key="recap", name="Recap", access_level="comment",
            )
            if r.ok:
                cid = (r.data or {}).get("canvas_id")
                if cid:
                    slackcode.set_recap_canvas_id(channel_id, cid)
                outcome["verdict"] = (":memo: Turned this session into a *Leadership recap* — "
                                      "it's in the *Recap* canvas tab (comment away; I'll keep it current).")
                outcome["audit"] = f":memo: Claude AI Slack Code recap canvas published in <#{channel_id}>."
                return
            logger.warning("recap canvas publish failed (%s) — falling back to message", r.error)
        except Exception:
            logger.exception("recap canvas path errored in %s — falling back to message", channel_id)

    # If the canvas path fails, the verdict is the full recap AS a message (the
    # fallback that always renders). Build it now so the pulse can finalize with it.
    lines_out = []
    for ln in markdown.splitlines():
        lines_out.append(f"*{ln[3:].strip()}*" if ln.startswith("## ") else ln)
    fallback_msg = (
        ":memo: *Leadership recap* — 5 parts, built from this session and the "
        "reliability dashboard, ready to share:\n\n" + "\n".join(lines_out)
    )

    # Pulse KEPT IN SYNC with the canvas: open the spinner, HOLD (spins alone —
    # nothing published yet), THEN publish/edit the canvas at the end of the pulse,
    # THEN finalize. Publishing the canvas BEFORE the hold was the bug where the
    # canvas tab appeared while the loading circle was still spinning. Same
    # hold-then-work order as run_scripted_beat / the first turn.
    line = _scenario_thinking_line(scenario, "recap")
    plan = {"mode": "plan", "title": "Thinking", "steps": [{"id": "do", "title": line}]}
    opened = await open_thinking_stream(client=client, logger=logger, channel_id=channel_id, plan=plan)

    stopped = slackcode.is_session_stopped(channel_id)
    if opened is not None and not stopped:
        try:
            await asyncio.sleep(_pulse_hold_s("recap"))
        except Exception:  # noqa: BLE001
            pass
        stopped = slackcode.is_session_stopped(channel_id)

    if not stopped:
        await _do_recap_work()  # canvas publishes/edits HERE, as the circle is about to resolve
    verdict = outcome["verdict"] or fallback_msg

    if opened is not None:
        streamer, used_plan = opened
        if slackcode.is_session_stopped(channel_id):
            await _close_open_stream_on_stop(opened, logger)
        else:
            await finalize_thinking_stream(streamer=streamer, plan=used_plan, logger=logger, response_text=verdict)
    elif not stopped:
        await client.chat_postMessage(channel=channel_id, markdown_text=verdict)
    if outcome["audit"] and not slackcode.is_session_stopped(channel_id):
        await audit_log(client, outcome["audit"])


# Keyword → action recognizer for the in-channel mention path. Deterministic
# (same style as slackcode.is_coding_task) so the demo beats trigger reliably.
# Returns the action name, or None if the mention is a normal question.
def recognize_code_action(text: str) -> str | None:
    """Map an in-code-channel message to a scripted action, or None.

    Order matters: 'patch'/'fix' beats 'check' (a "patch and re-check" line should
    patch, not just check)."""
    low = f" {text.lower()} "
    if any(k in low for k in (" patch", "generate a patched", " fix ", "apply the fix",
                              "fix it", "make it safe", "make it online-safe")):
        return "patch"
    if any(k in low for k in (" recap", " slides", " deck", " leadership",
                              "postmortem", "post-mortem", "post mortem", "write-up", "writeup")):
        return "recap"
    if any(k in low for k in ("dashboard", "metrics", "conversion", "it working",
                              "actually working", "the numbers")):
        return "metrics"
    # "check" covers every scenario's verify beat — keep these keywords in step
    # with each scenario's CHECK_LABEL (contrast / durability / load-test /
    # flake-stability / migration-safety) and its natural phrasings.
    if any(k in low for k in (
        "run a check", "run the check", " check ", "re-check", "recheck", "verify",
        "contrast", "accessib",                       # website_redesign
        "durability",                                 # billing_webhook
        "load test", "load-test", "loadtest", "under load", "at peak",  # checkout_incident
        "flake", "flaky", "stability", "stable",      # flaky_test
        "migration safety", "migration-safety", "safe to ship", "lock", "will it lock",  # sql_migration
    )):
        return "check"
    return None


# The pre-baked "Jennifer edit": a marketer asks, in natural language, for an
# expandable FAQ accordion on the WelloGuard page, and we serve a canned CSS-only
# diff + updated Preview INSTANTLY (no LLM turn — see run_faq_edit). Matched by
# NEAR-EXACT phrase, NOT the loose substring style of recognize_code_action: the
# pre-baked file corresponds to ONE specific ask, so a stray "faq" mention (or a
# request for a *different* FAQ, e.g. "with an animation") must fall through to the
# live edit path, not silently serve the canned accordion. website_redesign only.
_FAQ_CANONICAL_PHRASES = frozenset({
    "add an expandable faq section to the page",
    "add an expandable faq section",
    "add an expandable faq accordion to the page",
    "add a collapsible faq section to the page",
})


def recognize_faq_edit(text: str) -> bool:
    """True iff `text` is (near-exactly) the scripted "add an expandable FAQ" ask.

    Normalizes case + whitespace + surrounding punctuation, then requires an EXACT
    match against a small canonical set — tolerant of casing/spacing/a trailing
    period, but not of arbitrary keyword soup. This is deliberately stricter than
    recognize_code_action so the canned diff only fires on the scripted phrasing."""
    import re as _re
    norm = _re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" .!?")
    return norm in _FAQ_CANONICAL_PHRASES


async def run_code_action(
    client: AsyncWebClient, logger: Logger, cfg: slackcode.SlackCodeConfig, channel_id: str, action: str,
) -> None:
    """Dispatch a recognized action name to its helper."""
    if action == "patch":
        await run_patch(client, logger, cfg, channel_id)
    elif action == "check":
        await run_durability_check(client, logger, cfg, channel_id)
    elif action == "metrics":
        await run_show_metrics(client, logger, cfg, channel_id)
    elif action == "recap":
        await run_make_recap(client, logger, cfg, channel_id)


async def run_slack_code_session(
    *,
    client: AsyncWebClient,
    logger: Logger,
    cfg: slackcode.SlackCodeConfig,
    task_text: str,
    origin_channel_id: str,
    origin_thread_ts: str,
    origin_message_ts: str,
    user_id: str | None,
) -> bool:
    """Run a full Slack Code session for a coding-task mention.

    Returns True if the session was created and handled in a code channel,
    False if the caller should fall back to a normal thread reply (beta
    disabled, creation failed, etc.).
    """
    # session_id is the idempotency key — stable per origin message so a repeated
    # demo prompt reuses the channel instead of duplicating it.
    session_id = f"ses_{origin_channel_id}_{origin_message_ts}".replace(".", "")

    # Replay guard: Slack redelivers app_mention on ack-timeout, and each
    # redelivery has the same message ts. Claim the key up front; if it's already
    # in flight this is a redelivery — return True (we own it) so we neither
    # create a duplicate channel nor fall through to a thread reply.
    # Logged loudly so a live run reveals whether duplicates are (a) redeliveries
    # of one message [same ts, guard skips them], (b) distinct events [different
    # ts → likely >1 process/connection], or (c) stale leftovers from prior runs.
    claimed = slackcode.mark_session_started(session_id)
    logger.warning(
        "Slack Code gate: origin_ts=%s session=%s claimed=%s",
        origin_message_ts, session_id, claimed,
    )
    if not claimed:
        logger.warning("Slack Code session %s already started — skipping redelivery", session_id)
        return True

    name = slackcode.channel_name_for(task_text, cfg)

    created = await slackcode.create_channel(
        client,
        name=name,
        session_id=session_id,
        origin_channel_id=origin_channel_id,
        origin_message_ts=origin_message_ts,
    )
    if not created.ok:
        if created.feature_gated:
            logger.info("Slack Code disabled (%s) — falling back to thread reply", created.error)
            await audit_log(
                client,
                f":information_source: Claude AI Slack Code unavailable "
                f"(`{created.error}`) — replied in-thread instead.",
            )
        else:
            logger.warning("code-channel create failed: %s", created.error)
            await audit_log(
                client,
                f":warning: Claude AI `agents.conversations.create` → `{created.error}`. "
                f"Fell back to thread reply.",
            )
        # No channel was created — release the key so the caller's thread-reply
        # fallback runs and a later genuine retry isn't blocked.
        slackcode.clear_session_started(session_id)
        return False

    code_channel_id = (created.data or {}).get("channel_id") or (
        ((created.data or {}).get("channel") or {}).get("id")
    )
    if not code_channel_id:
        logger.warning("create ok but no channel_id in response; falling back")
        slackcode.clear_session_started(session_id)
        return False

    # Fresh session — clear any stale stop flag for this channel.
    # (Slack's auto "Context" backlink echo that arrives after creation is
    # recognized in both handlers via slackcode.is_context_echo from the event's
    # payload markers — no dependency on this channel_id being recorded anywhere.)
    slackcode.clear_session_stopped(code_channel_id)
    # We just created this channel, so it IS a code channel — seed the is_code_channel
    # cache True so the first in-channel follow-up doesn't spend a conversations.info
    # (and can't be tripped by a not-yet-propagated record_type right after creation).
    slackcode.mark_code_channel(code_channel_id)

    # From here the channel EXISTS, so any failure must surface IN the code
    # channel, not bubble to app_mentioned's outer catch (which would post the
    # bare "something went wrong" in the origin thread and leave the channel a
    # dead husk). Wrap the whole working session; log the exact failing line.
    try:
        return await _run_session_body(
            client=client, logger=logger, cfg=cfg, task_text=task_text,
            code_channel_id=code_channel_id, origin_channel_id=origin_channel_id,
            origin_thread_ts=origin_thread_ts, origin_message_ts=origin_message_ts,
            user_id=user_id, branch_seed=session_id,
        )
    except Exception:
        logger.exception("Slack Code session body failed in %s", code_channel_id)
        # If the session was stopped, honor the spec: post nothing further and
        # call no agents.* methods for this context (Slack owns the status now).
        if not slackcode.is_session_stopped(code_channel_id):
            try:
                await client.chat_postMessage(
                    channel=code_channel_id,
                    text="I hit a snag setting up this session. Let me know and I'll retry.",
                )
                await slackcode.set_session_status(client, code_channel_id, "active")
            except Exception:
                logger.exception("Slack Code failure-notice post also failed in %s", code_channel_id)
        await audit_log(
            client,
            f":warning: Claude AI Slack Code session body errored in <#{code_channel_id}> "
            f"— see logs. Channel left open.",
        )
        return True  # we own the channel; don't also reply in the origin thread


async def _run_session_body(
    *,
    client: AsyncWebClient,
    logger: Logger,
    cfg: slackcode.SlackCodeConfig,
    task_text: str,
    code_channel_id: str,
    origin_channel_id: str,
    origin_thread_ts: str,
    origin_message_ts: str,
    user_id: str | None,
    branch_seed: str,
) -> bool:
    """The working-session body, run inside run_slack_code_session's guard so any
    unexpected error surfaces in the code channel rather than the origin thread."""

    def _stopped() -> bool:
        if slackcode.is_session_stopped(code_channel_id):
            logger.info("session stopped for %s — ceasing work", code_channel_id)
            return True
        return False

    # Pick the demo story from the opening mention text (one running agent serves
    # all three), record it on the channel so every follow-up continues THIS story,
    # then load it. route_scenario never raises; unmatched build tasks → freeform.
    slug = route_scenario(task_text, default=cfg.scenario)
    slackcode.set_channel_scenario(code_channel_id, slug)
    scenario = load_scenario(slug)
    logger.warning("Slack Code scenario routed: channel=%s task=%r → %s",
                   code_channel_id, task_text[:60], slug)

    # Fidelity tier — pull the story's 1–2 origin people into the channel roster
    # FIRST (before any beats/artifacts), so the channel opens with humans already
    # present. Best-effort; resolves demoeng+<stem>_<orgnum>@… via lookupByEmail.
    # Their scripted chime-ins fire later regardless. No-op for freeform.
    _invite_status = await slackcode.invite_participants(
        client, code_channel_id, _participant_email_stems(scenario),
    )
    if _participant_email_stems(scenario):
        await audit_log(
            client,
            f":busts_in_silhouette: Claude AI Slack Code participants for <#{code_channel_id}> — {_invite_status}.",
        )

    # Branch uses the SCENARIO's prefix (checkout_incident → hotfix/, etc.), so the
    # context bar + diff head_branch match the story. Computed after routing.
    _slug = slackcode.channel_name_for(task_text, cfg).removeprefix(cfg.channel_name_prefix)
    branch = f"{_branch_prefix(cfg, scenario)}{_slug}"[:60]
    # Fresh session: clear per-channel state (patched flag, stored artifact) so a
    # reused channel id can't inherit a prior session's artifact/patch. (Scenario
    # is set above; leave it.)
    slackcode.clear_channel_patched(code_channel_id)
    slackcode.clear_channel_artifact(code_channel_id)
    slackcode.clear_recap_canvas_id(code_channel_id)

    # Give the session a clean human-readable title (agents.sessions.rename updates
    # both the channel name and the session title). The channel was auto-named from
    # a slug; a Title-Cased task reads better in the UI. Best-effort — never block
    # the session on a rename, and skip if the task text is empty.
    _title = task_text.strip()
    if _title:
        _title = (_title[:1].upper() + _title[1:])[:120]
        await slackcode.rename_session(client, code_channel_id, _title)

    await audit_log(
        client,
        f":sparkles: Claude AI opened Slack Code channel <#{code_channel_id}> "
        f"for <@{user_id}> (task: {task_text[:80]!r})",
    )

    # ---- working session ------------------------------------------------
    # Open the session chrome CONCURRENTLY (status + context bar + slash commands)
    # instead of three serial round-trips — each is an independent Web API call,
    # so gather() cuts the pre-reply latency to the slowest one. All are hardened
    # (slackcode.* never raise; return_exceptions guards the gather regardless).
    # Status "processing" gives Slack's own "Working…" UX + stop button. The
    # runtime commands are scenario-aware: the check command is /check-<label>.
    await asyncio.gather(
        slackcode.set_session_status(client, code_channel_id, "processing"),
        slackcode.set_context_bar(
            client, code_channel_id, _build_context_bar(cfg, branch=branch, phase="open", scenario=scenario),
        ),
        slackcode.set_commands(
            client, code_channel_id,
            [
                {"name": f"check-{scenario.CHECK_LABEL}",
                 "description": f"Run the {scenario.CHECK_LABEL} check on the current change"},
                {"name": "patch", "description": "Generate a patched version that fixes the check"},
                {"name": "show-metrics", "description": "Open the reliability dashboard"},
                {"name": "make-recap", "description": "Turn this session into a leadership recap"},
                {"name": "create-pr", "description": "Open a pull request for the current branch"},
                {"name": "run-tests", "description": "Run the test suite and report back"},
                {"name": "summarize", "description": "Summarize the session so far"},
            ],
        ),
        return_exceptions=True,
    )

    # ---- Live-artifact scenarios: the agent GENERATES the Code/Preview ---------
    # A code channel's artifacts are a living thing (the baseline behavior): the
    # agent builds a self-contained file and edits it every turn. For a live-
    # editable scenario we hand off to run_artifact_turn instead of publishing the
    # scripted diff/preview + running a prose-only agent call. Two kinds qualify:
    #   • freeform (is_seeded False): builds from scratch (snake game).
    #   • a seeded scenario WITH a preview page (website_redesign): seeds the
    #     WelloGuard page as turn-0, then edits it live.
    # A non-previewable seeded scenario (billing_webhook: diff+dashboard, multi-
    # file backend refactor — no single HTML artifact) keeps the scripted path.
    _arts = getattr(scenario, "ARTIFACTS", {"diff", "preview", "dashboard"})
    _live_editable = (not is_seeded(scenario)) or (
        "preview" in _arts and hasattr(scenario, "seed_filename")
    )
    if _live_editable:
        if _stopped():
            return True
        seed_html = None
        seed_filename = None
        if is_seeded(scenario) and "preview" in _arts:
            try:
                seed_html = scenario.preview_html(False)
                seed_filename = scenario.seed_filename()
            except Exception:  # noqa: BLE001 — a bad seed shouldn't block a freeform-style build
                logger.warning("seed load failed; starting artifact turn unseeded", exc_info=True)
                seed_html = seed_filename = None
        # Branch: freeform builds live on `master` (matches the screenshots'
        # `Branch master`); a seeded scenario keeps its feat/… branch so the
        # context bar and the diff head_branch agree.
        artifact_branch = branch if is_seeded(scenario) else "master"
        # Context bar with PR/CI (matches the scripted path's "work has begun" bar).
        await slackcode.set_context_bar(
            client, code_channel_id, _build_context_bar(cfg, branch=artifact_branch, phase="working", scenario=scenario),
        )
        # ARRIVAL beat: human chimes in as the turn begins (stall over the agent
        # build). No-op for freeform (no PARTICIPANTS); fires for seeded live-
        # editable stories like website_redesign.
        await _post_participant_beats(client, logger, code_channel_id, scenario, "arrival")
        result = await run_artifact_turn(
            client=client, logger=logger, cfg=cfg, channel_id=code_channel_id,
            task_text=task_text, first_turn=True, branch=artifact_branch,
            seed_html=seed_html, seed_filename=seed_filename, user_id=user_id,
        )
        # ON_ARTIFACTS beat: react to the just-published Code/Preview (only when an
        # artifact actually landed — no phantom reaction to a failed build).
        if not _stopped() and result.artifact_present:
            await _post_participant_beats(client, logger, code_channel_id, scenario, "on_artifacts")
        # Closing recap + Archive button (agent-design task-recap shape) — ONLY when
        # the turn actually produced/holds an artifact. If the agent hard-failed
        # (e.g. a 401 on an unentitled model) there's no file, and a "Built X, it's
        # in the Code tab" summary would be a visible lie. Use artifact_branch (what
        # the diff + context bar show) so the summary names the same branch.
        if not _stopped() and result.artifact_present:
            await _post_artifact_summary_and_archive(
                client, logger, code_channel_id, branch=artifact_branch, reply_ts=result.reply_ts,
            )
        return True

    # Scripted branch: the diff/dashboard are pre-built (scenario.base_diff etc.),
    # so we publish them EARLY — right after a short dwell, BEFORE the slow agent
    # turn — for a fast first paint (see the "FAST FIRST PAINT" block below).
    # WHICH tabs is per-scenario: scenario.ARTIFACTS declares them, so a backend
    # story publishes diff+dashboard (no page to preview) while a website story
    # publishes diff+preview+dashboard. The diff is always published. The recap
    # posts as a message, not a tab.
    if _stopped():
        return True
    # Open the "Thinking" stream FIRST — before the diff — so the spinner appears
    # the instant the channel opens and stays up through the diff publish AND the
    # whole (slow) agent call, then settles to a checkmark when the reply lands.
    # The order the user perceives: spinner up → brief dwell → diff/dashboard drop
    # → spinner keeps running while Claude writes the reply → settles with it.
    # Best-effort: if the beta rejects the stream, `_opened` is None and we fall
    # back to a plain reply post (the beat still lands, just without the live
    # spinner). The scenario's "open" thinking line is story-specific ("Reading
    # the incident thread and drafting the rollback…").
    _open_plan = {
        "mode": "plan", "title": "Thinking",
        "steps": [{"id": "do", "title": _scenario_thinking_line(scenario, "open")}],
    }
    _opened = await open_thinking_stream(
        client=client, logger=logger, channel_id=code_channel_id,
        recipient_user_id=user_id or None, plan=_open_plan,
    )

    # ARRIVAL beat: a human from the origin thread chimes in NOW — right as Claude's
    # Thinking spinner comes up and BEFORE the (slow) agent reply lands. This is the
    # "let's see what Claude comes back with" line, which reads as a real teammate
    # AND covers the cold-start latency as a natural stall. Best-effort/stop-guarded.
    await _post_participant_beats(client, logger, code_channel_id, scenario, "arrival")

    # Set the context bar to "working" NOW (repo · branch · PR) so the chips
    # populate while Claude thinks.
    artifacts = getattr(scenario, "ARTIFACTS", {"diff", "preview", "dashboard"})
    await slackcode.set_context_bar(
        client, code_channel_id, _build_context_bar(cfg, branch=branch, phase="working", scenario=scenario),
    )

    # FAST FIRST PAINT: publish the scripted diff/dashboard NOW — BEFORE the (slow)
    # agent turn — so the Code tab appears in a few seconds instead of after the
    # ~60s LLM reply. A short deliberate dwell first keeps the just-opened
    # "Thinking" spinner from looking decorative: spinner up → brief think → diff
    # drops → spinner keeps running through the agent turn → settles with the reply
    # (finalize_thinking_header + post_agent_reply below). The diff/dashboard are
    # SCRIPTED + agent-independent, so publishing them early never depends on the
    # model (this also means a slow/failed agent can't leave the channel bare).
    # (Earlier builds deferred the publish until AFTER the agent so the spinner had
    # something to represent — but that put the whole ~60s LLM turn between the user
    # and the first code block; the dwell buys the same "spinner isn't decorative"
    # feel without the wait.)
    # Publish SEQUENTIALLY (each setView mutates the channel's single
    # agent_session_views array; concurrent calls race — the "only 1 artifact" bug).
    # The diff is always published; preview/dashboard only if the scenario declares
    # them. Each returns a SlackCodeResult (never raises); captured + reported.
    if not _stopped():
        try:
            await asyncio.sleep(_prebuilt_dwell_s())
        except Exception:  # noqa: BLE001 — the dwell is cosmetic; never break the turn on it
            pass
    if not _stopped():
        reported: list[tuple[str, object]] = []
        diff_r = await _publish_diff(client, code_channel_id, scenario, branch=branch, patched=False)
        reported.append(("diff", diff_r))
        if "preview" in artifacts:
            prev_r = await _publish_preview(client, code_channel_id, scenario, patched=False)
            reported.append(("preview(html)", prev_r))
        if "dashboard" in artifacts:
            dash_r = await _publish_dashboard(client, code_channel_id, scenario)
            reported.append(("dashboard(block_kit)", dash_r))
        await _report_view_results(client, logger, code_channel_id, reported)

        # RESOLVE THE THINKING SPINNER NOW — the moment the diff is up, NOT after the
        # (slow) agent reply. The spinner represented "getting the first code up";
        # that's done, so settle it to `✓ Thinking` (a bare completed header) here.
        # The agent's prose reply follows later as its OWN plain chat.postMessage
        # below the diff, so the circle never keeps spinning over an already-published
        # diff — the bug where the diff appeared but the spinner ran on through the
        # whole agent turn. We null out `_opened` so the post-agent path doesn't try
        # to finalize a stream that's already closed. Best-effort.
        if _opened is not None:
            _s0, _p0 = _opened
            try:
                await finalize_thinking_header(streamer=_s0, plan=_p0, logger=logger)
            except Exception:  # noqa: BLE001 — closing the spinner is best-effort
                logger.warning("early finalize of Thinking spinner failed", exc_info=True)
            _opened = None

        # ON_ARTIFACTS beat: now that the diff/dashboard are visibly up (and the
        # spinner has settled), the second human reacts to them ("diff's up already —
        # hold it to a load test…"), tying the artifacts back to what the team cares
        # about. Best-effort.
        await _post_participant_beats(client, logger, code_channel_id, scenario, "on_artifacts")

    # Now run the agent for the in-character reply.
    deps = AgentDeps(
        client=client,
        user_id=user_id or "",
        channel_id=code_channel_id,
        thread_ts=None,  # top-level in the code channel (omit thread_ts)
        message_ts=origin_message_ts,
        user_token=None,
    )
    if _stopped():
        await _close_open_stream_on_stop(_opened, logger)
        return True
    user_display_name = await resolve_user_name(client, user_id) if user_id else None

    # Feed the origin conversation to the agent as context. Without this the code
    # channel is empty and the model gets a naked coding imperative — it can't know
    # which repo/cron/target the channel was discussing and breaks persona.
    transcript = await _fetch_origin_context(
        client, logger,
        origin_channel_id=origin_channel_id,
        origin_thread_ts=origin_thread_ts,
        origin_message_ts=origin_message_ts,
    )
    # Prime the session with the scenario's decision rationale so a later
    # "@Claude why did you …?" answers FROM recorded reasoning, not improvisation.
    # (A scripted chat.postMessage would NOT enter the SDK session — only turns
    # routed through the agent populate resumable history — so the rationale must
    # ride the first agent_input.)
    provenance_block = ""
    try:
        provenance_block = scenario.render_provenance()
    except Exception:  # noqa: BLE001 — provenance is optional; never block the session
        provenance_block = ""

    parts: list[str] = []
    if transcript:
        parts.append(
            "You're picking up a coding task raised in a Slack channel you're part "
            "of. Here's the recent discussion that led to it:\n\n"
            f"{transcript}"
        )
    if provenance_block:
        parts.append(provenance_block)
    parts.append(f"The task: {task_text}")
    agent_input = "\n\n".join(parts) if (transcript or provenance_block) else task_text

    # Run the agent for the in-character reply. This is the one LIVE, fragile
    # piece of the session (it drives the host CLI against the Bedrock gateway and
    # can be slow/time out). The rest of the session — diff, PR/CI context bar,
    # closing summary — is SCRIPTED demo chrome that does NOT depend on the agent.
    # So a failed/slow agent call must NOT block the visible artifacts: we treat
    # the reply as best-effort, fall back to an in-character line, and ALWAYS go
    # on to publish the diff + summary. (Previously the diff was gated behind
    # run_agent succeeding, so a timeout left the channel bare — the bug that made
    # only the sessions whose agent happened to succeed show diffs.)
    response_text = ""
    new_session_id = None
    if not _stopped():
        try:
            response_text, new_session_id = await run_agent_offloop(
                agent_input, session_id=None, deps=deps, user_display_name=user_display_name,
                with_tools=False,  # emoji-reaction tool is meaningless here; skip its SDK-MCP wiring
            )
        except Exception as e:
            logger.warning("run_agent failed in code channel (%s) — continuing with scripted artifacts",
                           type(e).__name__, exc_info=True)
            await audit_log(
                client,
                f":warning: Claude AI Slack Code run_agent_error={type(e).__name__} "
                f"in <#{code_channel_id}> — proceeded with scripted diff/summary.",
            )

    if _stopped():
        await _close_open_stream_on_stop(_opened, logger)
        return True
    # Persist the resumable session id whenever the agent returned one — do NOT
    # gate this on response_text being non-empty. A slow/empty first reply over
    # the Bedrock gateway still produced a provenance-primed session, and gating
    # it here was the bug that made a later "why did you …?" follow-up improvise
    # (the session mapping was never stored, so the resume found nothing).
    if new_session_id:
        session_store.set_session(code_channel_id, "", new_session_id)

    # (The diff/dashboard + on_artifacts beat were already published above, BEFORE
    # this agent call, for a fast first paint — see the "FAST FIRST PAINT" block.)

    # Resolve the reply text ONCE: the agent's answer, or an in-character fallback
    # that points at the tabs we already published (so a slow/empty agent reply
    # still reads naturally).
    if response_text.strip():
        reply_markdown = response_text
    else:
        _arts = getattr(scenario, "ARTIFACTS", {"diff", "preview", "dashboard"})
        extra = ""
        if "preview" in _arts and "dashboard" in _arts:
            extra = " — plus a *Preview* and a *Dashboard* tab"
        elif "preview" in _arts:
            extra = " — plus a *Preview* tab"
        elif "dashboard" in _arts:
            extra = " — plus a *Dashboard* tab"
        _repo_name = getattr(scenario, "REPO", None) or cfg.fake_repo
        reply_markdown = (
            f"On it — I've taken a first pass at this in `{_repo_name}` on `{branch}`. "
            f"The diff's up in the *Code* tab{extra}. Take a look and tell me what to adjust."
        )

    # The story's ordered beats: `✓ Thinking` → the diff → the reply. The spinner
    # was already settled to a bare `✓ Thinking` header right after the diff
    # published (see the FAST FIRST PAINT block; `_opened` is None here now). So the
    # only thing left is to post the reply as its OWN top-level message, below the
    # already-settled diff. We post it PLAIN (chat.postMessage), not streamed, so it
    # appears fully-formed at once — no "bubble appears, then content fills in a beat
    # later" lag, and no second "Thinking" block rendering inside the reply. Strip any
    # agent ```taskplan``` fence first (we own the single Thinking block above).
    _agent_plan, reply_markdown = extract_plan(reply_markdown)
    if not (reply_markdown or "").strip():
        reply_markdown = "Done — take a look in the *Code* tab."
    try:
        await client.chat_postMessage(channel=code_channel_id, markdown_text=reply_markdown)
    except Exception:
        logger.warning("scripted reply post failed", exc_info=True)

    if _stopped():
        return True
    # Post a closing summary WITH an Archive button (the spec's wrap-up pattern:
    # post summary, offer an archive action, archive on confirm), then go idle.
    # Name only the artifact tabs this scenario actually published.
    _artifacts = getattr(scenario, "ARTIFACTS", {"diff", "preview", "dashboard"})
    tab_phrases = ["the *Code* diff"]
    if "preview" in _artifacts:
        tab_phrases.append("a *Preview* of the result")
    if "dashboard" in _artifacts:
        tab_phrases.append("a *Dashboard* of the numbers")
    tabs_sentence = _join_phrases(tab_phrases)
    summary = await client.chat_postMessage(
        channel=code_channel_id,
        markdown_text=(
            f":white_check_mark: First pass up on `{branch}`. Check out the artifacts — the plan, "
            f"{tabs_sentence}. Before you ship, run `/check-{scenario.CHECK_LABEL}` (or just ask "
            f"me to run a check) to verify it. Archive this channel when you're happy."
        ),
    )
    summary_ts = summary.get("ts")
    # Record the summary as the channel's summary_message WHILE the session is live
    # (not only at archive) so it's discoverable mid-session. Best-effort; a stop
    # bars further agents.* calls, so guard it.
    if summary_ts and not _stopped():
        await slackcode.set_properties(
            client, code_channel_id, summary_message={"message_ts": summary_ts},
        )
    # A stop landing between the summary and the archive-button post must suppress
    # the button too (no further chat.postMessage after a stop).
    if _stopped():
        return True
    # The Archive button carries channel_id + summary_ts so the action handler can
    # call agents.conversations.archive with summary_message_ts (recorded as the
    # channel's summary_message before archival).
    if summary_ts:
        try:
            await client.chat_postMessage(
                channel=code_channel_id,
                text="Archive this session when you're done.",
                blocks=[{
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Archive channel"},
                        "style": "primary",
                        "action_id": "slackcode_archive",
                        "value": f"{code_channel_id}|{summary_ts}",
                    }],
                }],
            )
        except Exception:
            logger.warning("failed to post archive button; session still idle", exc_info=True)

    if _stopped():
        return True
    await slackcode.set_session_status(client, code_channel_id, "active")
    await audit_log(
        client,
        f":checkered_flag: Claude AI Slack Code session ready in <#{code_channel_id}> "
        f"(summary ts=`{summary_ts}`). Awaiting user.",
    )
    return True


def render_plan_as_text(plan: dict) -> str:
    """Render a taskplan dict as a compact '✓ step' text block, for a top-level
    chat.postMessage (Bolt's streaming helper can't post top-level in a code
    channel, so we don't animate here — we show the finished checklist)."""
    lines = []
    title = plan.get("title")
    if title:
        lines.append(f"*{title}*")
    for s in plan.get("steps", []):
        t = s.get("title") or ""
        det = s.get("details")
        lines.append(f":white_check_mark: {t}" + (f" — _{det}_" if det else ""))
    return "\n".join(lines)


async def post_agent_reply_top_level(
    *, client: AsyncWebClient, logger: Logger, channel_id: str, response_text: str,
) -> str | None:
    """Post the agent reply top-level in a code channel.

    Prefers a STREAMED reply (chat.startStream, top-level) so a leading
    ``taskplan`` "plan" fence renders as Slack's native collapsible
    "Thinking… → Thinking completed" plan block — the disclosure in the product
    screenshots. Falls back to plain chat.postMessage if streaming fails.

    Handles the two optional fences:
      - taskplan → streamed as the animated plan block (or a compact '✓' preface
        on the postMessage fallback)
      - blockkit → the answer card (posted in chat.stopStream, which is the only
        stream method that accepts blocks)
      - otherwise → plain markdown text

    Returns the ts of the posted/streamed message (for archive summary_message_ts),
    or None on total failure.
    """
    return await stream_reply_top_level(
        client=client, logger=logger, channel_id=channel_id, response_text=response_text,
    )


# ---------------------------------------------------------------------------
# Top-level streamed reply → the "Thinking" plan block
# ---------------------------------------------------------------------------
# Slack Code replies are top-level (no thread_ts). Bolt's say_stream can't post
# top-level in a code channel, but the raw chat.*Stream Web API methods can — the
# lifecycle doc explicitly sanctions top-level streaming (omit thread_ts, or the
# legacy "0" sentinel). We stream via chat.startStream(task_display_mode="plan")
# so the persona's ```taskplan``` renders as the collapsible plan block, animate
# the steps with chat.appendStream, then finalize the SAME message with the prose
# / blockkit answer via chat.stopStream (the only stream method that takes blocks).


class _TopLevelStreamer:
    """Minimal adapter exposing the subset of Bolt's AsyncChatStream API that
    render_plan uses (`.append(chunks=...)`), backed by the raw chat.*Stream Web
    API so it can post TOP-LEVEL in a code channel. render_plan is written against
    Bolt's streamer; this lets us reuse it unchanged.

    `ts` is captured from chat.startStream and reused for append/stop. thread_ts
    is omitted so the stream is a top-level channel message, not a thread reply.

    chat.startStream to a channel REQUIRES recipient_team_id (and
    recipient_user_id) — without them the stream call is rejected and the reply
    silently falls back to a plain post, which also strands the message-level
    loading indicator. We look up the team id once (cached auth.test) and pass
    the human participant as recipient_user_id.
    """

    def __init__(
        self,
        client: AsyncWebClient,
        channel_id: str,
        *,
        task_display_mode: str | None,
        recipient_user_id: str | None = None,
    ):
        self._client = client
        self._channel = channel_id
        self._mode = task_display_mode
        self._recipient_user_id = recipient_user_id
        self.ts: str | None = None
        self._closed = False  # set once stop() runs, so a second stop() is a no-op
        # Slack's streaming contract: a stream must be APPENDED and STOPPED in the
        # SAME payload mode it was started/first-appended in — chunks vs markdown_text.
        # Mixing them returns `streaming_mode_mismatch` and the stop is REJECTED, so
        # the message-level "Stop agent" indicator never clears (the stuck-spinner
        # bug seen live). We append the "Thinking" plan via chunks, so the stream is
        # in chunks mode; stop() must then also send chunks (the final prose becomes
        # a MarkdownTextChunk), never a top-level markdown_text. Track the mode from
        # the first append and honor it in stop().
        self._used_chunks = False

    @property
    def is_open(self) -> bool:
        """True if the stream was started (has a ts) and not yet stopped — i.e. its
        loading indicator is still live and needs a stopStream to clear it."""
        return bool(self.ts) and not self._closed

    async def start(self) -> None:
        # Recipient args are required for a channel stream; look up team_id once.
        ident = await slackcode.get_bot_identity(self._client)
        kwargs: dict = {
            "channel": self._channel,
            "thread_ts": "0",  # top-level per the Slack Code lifecycle doc
            "task_display_mode": self._mode,
        }
        team_id = ident.get("team_id")
        if team_id:
            kwargs["recipient_team_id"] = team_id
        # Prefer the human participant; fall back to the bot's own id so the
        # required arg is always present even when we don't know the user.
        recipient = self._recipient_user_id or ident.get("user_id")
        if recipient:
            kwargs["recipient_user_id"] = recipient
        resp = await self._client.chat_startStream(**kwargs)
        # `not_in_channel` is recoverable: join the channel once and retry the
        # stream. (The bot is auto-invited to channels it creates, so this mainly
        # covers an added-to-existing-channel session or an edge race.)
        if not bool(resp.get("ok", True)) and resp.get("error") == "not_in_channel":
            import logging as _lg
            _log = _lg.getLogger("claude-ai-bot")
            _log.info("startStream not_in_channel — joining and retrying once")
            if await _ensure_member(self._client, _log, self._channel):
                resp = await self._client.chat_startStream(**kwargs)
        # Check the response, don't just fish for a ts. A REJECTED startStream
        # (e.g. not_in_channel, feature_disabled, invalid_thread_ts, or a missing
        # recipient arg) returns ok:false with no ts — leaving self.ts=None so every
        # append/stop no-ops. That is invisible unless we log it, and it is the
        # prime suspect for a "Stop agent" indicator that Slack raised but we never
        # tore down. Log the exact error loudly (shows up in the bot's stderr) so a
        # live run reveals WHY streaming fell back to a plain post.
        ok = bool(resp.get("ok", True))  # slack_sdk resp is dict-like; default-true if absent
        self.ts = resp.get("ts") if ok else None
        if not self.ts:
            import logging as _lg
            _lg.getLogger("claude-ai-bot").warning(
                "chat.startStream did not open a stream: ok=%s error=%s (recipient_team=%s recipient_user=%s). "
                "The 'Stop agent' indicator may be left up by Slack; falling back to a plain reply.",
                ok, resp.get("error"), bool(kwargs.get("recipient_team_id")), bool(kwargs.get("recipient_user_id")),
            )

    async def append(self, *, chunks=None, markdown_text=None) -> None:
        if not self.ts:
            return
        kwargs: dict = {"channel": self._channel, "ts": self.ts}
        if chunks is not None:
            kwargs["chunks"] = chunks
            self._used_chunks = True  # this stream is now in chunks mode
        if markdown_text is not None:
            kwargs["markdown_text"] = markdown_text
        await self._client.chat_appendStream(**kwargs)

    async def stop(self, *, markdown_text=None, blocks=None) -> None:
        # Idempotent: once the stream is closed, a second stop() is a no-op. The
        # success path closes it with the answer; the finally-block safety net may
        # then call stop() again on a mid-turn-stop exit — the guard makes that
        # extra call harmless (no duplicate chat.stopStream on an ended stream).
        if not self.ts or self._closed:
            return
        kwargs: dict = {"channel": self._channel, "ts": self.ts}
        # Honor the stream's payload mode. If we appended the "Thinking" plan via
        # chunks (the normal path), the stream is in chunks mode and Slack REJECTS a
        # top-level markdown_text stop with `streaming_mode_mismatch` — which leaves
        # the "Stop agent" indicator spinning forever. So deliver the final prose as
        # a MarkdownTextChunk inside chunks= instead. `blocks` is a distinct
        # top-level stopStream arg (rendered at the bottom of the finalized message)
        # and is NOT subject to the markdown-vs-chunks rule, so it passes through.
        if markdown_text is not None:
            if self._used_chunks:
                from slack_sdk.models.messages.chunk import MarkdownTextChunk
                kwargs["chunks"] = [MarkdownTextChunk(text=markdown_text)]
            else:
                kwargs["markdown_text"] = markdown_text
        if blocks is not None:
            kwargs["blocks"] = blocks
        await self._client.chat_stopStream(**kwargs)
        self._closed = True


# A tiny "Thinking…" plan shown WHILE the agent works, when we don't yet have the
# agent's own taskplan (it hasn't replied). Steps read as the work actually in
# flight in run_artifact_turn: understand the ask → build/edit the file → update
# the Code + Preview tabs. Past-tense titles match the persona's plan style; they
# start in the spinner state and flip to checkmarks once the work is done.
_WORKING_PLAN = {
    "mode": "plan",
    "title": "Thinking",
    "steps": [
        {"id": "understand", "title": "Read the request and the current file"},
        {"id": "build", "title": "Wrote the updated file"},
        {"id": "publish", "title": "Refreshed the Code and Preview tabs"},
    ],
}


async def _close_open_stream_on_stop(
    opened: tuple[_TopLevelStreamer, dict] | None, logger: Logger,
) -> None:
    """Close a Thinking stream (from `open_thinking_stream`) when the session was
    stopped mid-turn, so the message-level loading spinner doesn't strand.

    Per the lifecycle spec, after `agent_session_stopped` we make NO
    `agents.sessions.*` calls (Slack owns the status then) — but `chat.stopStream`
    is a plain `chat.*` call, and closing the stream is precisely what clears the
    loading indicator, so we still do it (best-effort). No plan-complete flip: on a
    stop we just end the stream with a terse note rather than a fake checkmark."""
    if opened is None:
        return
    streamer, _plan = opened
    try:
        await streamer.stop(markdown_text="_Stopped._")
    except Exception:  # noqa: BLE001 — best-effort; a failed close only risks a lingering spinner
        logger.exception("closing Thinking stream after stop failed")


async def open_thinking_stream(
    *, client: AsyncWebClient, logger: Logger, channel_id: str,
    recipient_user_id: str | None = None, plan: dict | None = None,
) -> tuple[_TopLevelStreamer, dict] | None:
    """Open a top-level stream and paint a "Thinking" plan with its steps in the
    `in_progress` (spinner) state, BEFORE the real work runs. Returns
    ``(streamer, plan)`` — pass BOTH back to `finalize_thinking_stream` so the
    SAME plan (same step ids) is the one flipped to complete. Returns None if the
    stream couldn't be opened (caller then posts a plain reply).

    Returning the plan is load-bearing: Slack updates a task card in place only
    when a later chunk reuses the same ``id``. If finalize completed a DIFFERENT
    plan (e.g. the agent's own taskplan, with different ids), these pending cards
    would never receive a `complete` chunk and would spin forever — the exact bug
    this contract prevents. Because the stream stays open, Slack keeps the
    message-level loading indicator up the whole time the agent is working.
    """
    use_plan = plan or _WORKING_PLAN
    streamer = _TopLevelStreamer(
        client, channel_id, task_display_mode="plan", recipient_user_id=recipient_user_id,
    )
    try:
        await streamer.start()
        if not streamer.ts:
            return None
        await render_plan_steps_pending(streamer, use_plan)
        return streamer, use_plan
    except Exception:
        logger.exception("open_thinking_stream failed; caller will post a plain reply")
        # Best-effort close so we don't strand a half-open stream's indicator.
        try:
            await streamer.stop(markdown_text="…")
        except Exception:
            pass
        return None


async def finalize_thinking_stream(
    *, streamer: _TopLevelStreamer, plan: dict, logger: Logger, response_text: str,
) -> str | None:
    """Finalize a stream opened by `open_thinking_stream`: flip the SAME plan's
    steps (the one returned by open_thinking_stream, matching ids) from spinner to
    complete, then close the stream with the answer (prose or a Block Kit card).

    We complete the plan we ALREADY showed — never the agent's own taskplan. The
    agent's ```taskplan``` fence (if any) is stripped from the answer here: we own
    the single "Thinking" checklist and driving it from one fixed-id plan is what
    makes the spinners settle to checkmarks in place. Closing the stream is what
    ends the message-level loading indicator, so it ALWAYS runs.
    """
    # Strip any agent-emitted taskplan fence — we render our own single checklist,
    # so a second plan block would just stack (the "two plans" bug). Keep only the
    # prose / card for the finalized answer.
    _agent_plan, rest = extract_plan(response_text)
    card_blocks, _tier = extract_blocks(rest)
    if card_blocks is not None and validate_blocks(card_blocks):
        card_blocks = None  # invalid card → prose

    try:
        await render_plan_steps_complete(streamer, plan)
    except Exception:
        logger.exception("render_plan_steps_complete failed; finalizing with answer only")
    finally:
        # Always close the stream — this is what ends the loading indicator.
        try:
            if card_blocks is not None:
                await streamer.stop(blocks=card_blocks)
            else:
                await streamer.stop(markdown_text=(rest or "Done."))
        except Exception:
            logger.exception("stopStream failed; stream may show as still streaming")
    return streamer.ts


async def finalize_thinking_header(
    *, streamer: _TopLevelStreamer, plan: dict, logger: Logger,
) -> str | None:
    """Finalize a "Thinking" stream to a BARE completed header — flip its steps to
    complete and close the stream with NO prose body.

    This is for the SCRIPTED session-start turn, where we want the settled layout
    to read as three ordered beats: `✓ Thinking` (this collapsed header) → the diff
    → the reply posted as a SEPARATE message BELOW the diff. If we closed the stream
    WITH the reply prose (finalize_thinking_stream), that prose would be pinned
    inside the Thinking message — which was created before the diff — so the reply
    could never appear after the diff (the "reply above the diff" incohesion).
    Closing with an empty body (stop() with no markdown_text/blocks) settles the
    plan to a checkmark header and leaves the prose to a later top-level post.
    Closing the stream is what ends the loading indicator, so it ALWAYS runs.

    NOTE — separating the reply from the "Thinking" block MATCHES Slack's code-
    channel guidance; it is not a deviation. The Code channel lifecycle doc (beta,
    api.slack.com/partners/code-channels-lifecycle) lists the working session as
    DISTINCT steps: setView (the diff/artifact — a channel TAB, not a timeline
    message), then a reply via chat.postMessage, then a final summary via
    chat.postMessage. So the reply is meant to be its own message; streaming it
    top-level (chat.startStream, thread_ts omitted) is also explicitly sanctioned
    there. We keep the "Thinking" plan as its own collapsed header and let the
    reply post separately (below), rather than closing the stream WITH the reply
    prose (finalize_thinking_stream). The general assistant-DM streaming pattern
    (plan + answer in one streamed message) is the wrong reference for a code
    channel — that's for a DM with no separate artifact surface. Closing the stream
    is what ends the loading indicator, so it ALWAYS runs.
    """
    try:
        await render_plan_steps_complete(streamer, plan)
    except Exception:
        logger.exception("render_plan_steps_complete failed; closing header anyway")
    finally:
        try:
            await streamer.stop()  # no prose — bare "✓ Thinking" header
        except Exception:
            logger.exception("stopStream failed; stream may show as still streaming")
    return streamer.ts


# ---------------------------------------------------------------------------
# Thinking pulse for the SCRIPTED beats (check / patch / metrics / recap)
# ---------------------------------------------------------------------------
# The scripted actions are deterministic and near-instant, so they don't run the
# agent — but the demo wants Claude to visibly "think" on EVERY turn. So each
# scripted beat opens a top-level stream showing ONE honest, story-specific line
# (from the scenario's thinking_line), holds briefly, then finalizes that SAME
# message with the verdict. One coherent "Thinking → result" block, exactly like
# the artifact turn, with no invented multi-step work. Degrades gracefully: if the
# stream can't open, the caller falls back to a plain chat.postMessage of the
# verdict (so a beat never silently drops).

# How long each scripted beat holds the "Thinking" pulse before revealing the
# verdict. These are DELIBERATE dwells (the work itself is instant/canned), tuned
# per action so the beat feels like the thing it claims to be doing: a load-test /
# check replaying peak traffic reads as heavier than just pulling a dashboard.
# Bumped from a flat 1.1s (too quick to point at in a live demo). A missing action
# falls back to _PULSE_HOLD_DEFAULT.
_PULSE_HOLD_DEFAULT = 3.0
_PULSE_HOLD_BY_ACTION = {
    "check": 5.0,    # replaying peak traffic against the change — the heaviest beat
    "patch": 4.0,    # reworking the code path
    "metrics": 2.5,  # just reading numbers off a dashboard — lightest
    "recap": 3.0,    # writing up the summary
}


def _pulse_hold_s(action: str) -> float:
    """Per-action dwell for a scripted beat's Thinking pulse (see _PULSE_HOLD_BY_ACTION)."""
    return _PULSE_HOLD_BY_ACTION.get(action, _PULSE_HOLD_DEFAULT)


# --- Fast first paint: the pre-built-diff dwell (the "fast first paint" work) ---
# SCRIPTED stories publish their pre-built diff/dashboard BEFORE the agent runs so
# the Code tab paints in a few seconds instead of after the ~60s LLM turn (see
# _run_session_body). PREBUILT_DWELL_S is a short deliberate spinner hold BEFORE
# that instant diff drops (scripted branch + the FAQ edit) so the just-opened
# "Thinking" spinner isn't decorative (spinner up → brief think → diff → settle).
# Read via a function (not a module constant) so a launch-time override is honored.
#
# NOTE: the FREEFORM / unscripted-edit path is deliberately NOT capped — there the
# diff IS the agent's generated file, so the wait is the real build; the spinner
# stays up showing honest working steps and settles when the diff lands. A cap
# there could only produce a scaffold, which can't heal to the real file without a
# second prompt (tried it — the follow-up edited the scaffold, not a real build).
# Only RUN_AGENT_TIMEOUT (agent.py, default 300s) bounds a true hang.
_PREBUILT_DWELL_DEFAULT_S = 3.0


def _env_float(name: str, default: float, *, min_value: float = 0.0) -> float:
    """Read a float from the env, falling back to ``default`` for an unset / empty /
    non-numeric value or one below ``min_value``."""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return v if v >= min_value else default


def _prebuilt_dwell_s() -> float:
    """Deliberate spinner hold before an instant pre-built diff (env override:
    SLACK_CODE_PREBUILT_DWELL_S; default 3s). 0 is allowed (skip the dwell)."""
    return _env_float("SLACK_CODE_PREBUILT_DWELL_S", _PREBUILT_DWELL_DEFAULT_S, min_value=0.0)


def _scenario_thinking_line(scenario, action: str) -> str:
    """The one-line Thinking pulse for a scripted action, from the scenario (which
    tailors it to its story), with a generic fallback for a scenario that predates
    thinking_line."""
    getter = getattr(scenario, "thinking_line", None)
    if callable(getter):
        try:
            line = getter(action)
            if line:
                return line
        except Exception:  # noqa: BLE001 — never break a beat on a bad line
            pass
    return {
        "check": "Running the check…",
        "patch": "Applying the patch…",
        "metrics": "Pulling the numbers…",
        "recap": "Writing the recap…",
    }.get(action, "Working on it…")


async def run_scripted_beat(
    *, client: AsyncWebClient, logger: Logger, channel_id: str, scenario, action: str,
    work, verdict_markdown: str, recipient_user_id: str | None = None,
) -> None:
    """Run a scripted beat with a visible Thinking pulse, KEPT IN SYNC with its work.

    Sequence (this ORDER is the point — see below):
      1. Open the "Thinking" stream — the spinner appears and spins ALONE.
      2. HOLD for a per-action dwell — the "working" beat, with NOTHING published yet.
      3. Run `work()` — the deterministic side effects (publish/refresh the Code /
         Dashboard / Canvas tab, flip the context bar). The artifact appears HERE,
         at the END of the pulse.
      4. Finalize the SAME streamed message with `verdict_markdown` — the circle
         resolves to a checkmark immediately after the artifact lands.

    Why hold-THEN-work, not work-then-hold: if we published the artifact first and
    then held, the tab/canvas would pop in while the loading circle was still
    spinning — the "output appeared before the thinking finished" bug (seen on
    /make-recap: the canvas showed mid-spin). Doing the work at the end keeps the
    artifact and the spinner's resolution in sync: spinner (working…) → artifact →
    resolve. This mirrors the session-start first turn, which publishes the diff
    AFTER the agent runs.

    If the stream can't open (beta gated / rejected), we still run `work()` and post
    the verdict as a plain message — the beat always lands. `work` may be a
    coroutine or None. If the session is stopped during the hold, we skip the work
    and close the stream (chat.stopStream is allowed after a stop).

    VERDICT-IN-PLACE is intentional: a scripted beat's artifact is a TAB (setView /
    canvas), not a timeline message, so nothing is interleaved between the spinner
    and the verdict — closing the "Thinking" stream WITH the verdict (one message,
    startStream → appendStream → stopStream) reads cleanly. The lifecycle doc (beta)
    treats a reply as its own chat.postMessage, so splitting would ALSO be
    conformant; we don't, because there's no timeline-ordering problem here. (The
    session-start first turn DOES split — it publishes a fresh diff as a distinct
    step and the reply follows it — see finalize_thinking_header.)
    """
    line = _scenario_thinking_line(scenario, action)
    plan = {"mode": "plan", "title": "Thinking", "steps": [{"id": "do", "title": line}]}
    opened = await open_thinking_stream(
        client=client, logger=logger, channel_id=channel_id,
        recipient_user_id=recipient_user_id, plan=plan,
    )

    stopped = slackcode.is_session_stopped(channel_id)

    # Hold FIRST — the spinner spins alone (the "working" beat) before anything is
    # published, so the artifact doesn't appear mid-spin. Only when the stream is up.
    if opened is not None and not stopped:
        try:
            await asyncio.sleep(_pulse_hold_s(action))  # per-action dwell (see _PULSE_HOLD_BY_ACTION)
        except Exception:  # noqa: BLE001
            pass
        stopped = slackcode.is_session_stopped(channel_id)

    # Do the real (deterministic) work at the END of the pulse, so the artifact
    # (tab/canvas/bar) lands right as the circle is about to resolve. Skip if a stop
    # arrived during the hold.
    if work is not None and not stopped:
        try:
            await work()
        except Exception:
            logger.exception("scripted beat %s work() failed in %s", action, channel_id)

    if opened is not None:
        streamer, used_plan = opened
        if slackcode.is_session_stopped(channel_id):
            # Stopped mid-beat — close the stream bare (no verdict) so the loading
            # indicator doesn't strand. (chat.stopStream is allowed after a stop.)
            await _close_open_stream_on_stop(opened, logger)
        else:
            # Finalize the SAME message: flip the step to complete + close with the verdict.
            await finalize_thinking_stream(
                streamer=streamer, plan=used_plan, logger=logger, response_text=verdict_markdown,
            )
    elif not stopped:
        # Stream didn't open — plain post so the verdict still lands.
        try:
            await client.chat_postMessage(channel=channel_id, markdown_text=verdict_markdown)
        except Exception:
            logger.exception("scripted beat %s verdict post failed in %s", action, channel_id)


async def stream_reply_top_level(
    *, client: AsyncWebClient, logger: Logger, channel_id: str, response_text: str,
    recipient_user_id: str | None = None,
) -> str | None:
    """Single-shot streamed reply: open a stream, render a leading ``taskplan``
    (spinner → settle per step), and finalize with the answer. Used when the
    reply is already in hand and there's no separate work window to bracket
    (the plan animates after the fact, like the DM path).

    Returns the streamed message's ts (for archive), or None on failure. On any
    streaming error, falls back to a plain chat.postMessage of the compact static
    plan preface + answer (the pre-existing behavior) so a reply always lands.
    """
    plan, rest = extract_plan(response_text)
    if plan is not None and validate_plan(plan):
        # Malformed plan → drop it, render the answer only (matches finalize.py).
        logger.warning("code-channel taskplan invalid; streaming answer only")
        plan = None

    card_blocks, tier = extract_blocks(rest)
    if card_blocks is not None and validate_blocks(card_blocks):
        card_blocks = None  # invalid card → fall back to prose

    mode = ("plan" if plan.get("mode") == "plan" else "timeline") if plan else None

    # --- Preferred path: real top-level stream (the Thinking plan block) -------
    streamer = _TopLevelStreamer(
        client, channel_id, task_display_mode=mode, recipient_user_id=recipient_user_id,
    )
    try:
        await streamer.start()
        if streamer.ts:
            try:
                if plan is not None:
                    try:
                        await render_plan(streamer, plan)
                    except Exception:
                        logger.exception("render_plan failed mid-stream; finalizing with answer")
            finally:
                # Always close the stream so the loading indicator ends.
                if card_blocks is not None:
                    await streamer.stop(blocks=card_blocks)
                else:
                    await streamer.stop(markdown_text=(rest or "Done."))
            return streamer.ts
    except Exception:
        logger.exception("top-level stream failed; falling back to chat.postMessage")

    # --- Fallback: plain postMessage (never leave the user without a reply) ----
    try:
        if plan is not None:
            preface = render_plan_as_text(plan)
            if preface:
                await client.chat_postMessage(channel=channel_id, markdown_text=preface)
        if card_blocks is not None:
            resp = await client.chat_postMessage(channel=channel_id, blocks=card_blocks, text="")
        else:
            resp = await client.chat_postMessage(channel=channel_id, markdown_text=(rest or "Done."))
        return resp.get("ts")
    except Exception:
        logger.exception("code-channel reply post failed; plain-text fallback")
        resp = await client.chat_postMessage(channel=channel_id, text=response_text or "Done.")
        return resp.get("ts")


# ---------------------------------------------------------------------------
# Live artifact turn engine (the "living" Code + Preview tabs)
# ---------------------------------------------------------------------------
# The baseline behavior of EVERY code channel: the agent generates a complete
# self-contained file (emitted in a ```html``` fence), which we publish to the
# Code (diff) + Preview (html) tabs and store; each subsequent turn regenerates
# the file, we diff it against the stored version (incremental diff) and re-publish
# both tabs (setView is a replace/upsert). Seeded scenarios (website_redesign)
# hand the agent a turn-0 file to edit; freeform channels build from scratch.


@dataclass
class ArtifactTurnResult:
    """Outcome of run_artifact_turn.

    reply_ts:          ts of the streamed/posted reply (for archive), or None.
    artifact_present:  True if the channel has a published artifact AFTER the turn
                       (seed published, or the agent produced a file, or one was
                       already there). The session-start summary ("Built X, it's
                       in the Code tab") must only fire when this is True — a failed
                       agent call (e.g. a 401 on an unentitled model) produces no
                       artifact, and claiming one anyway is a lie the user sees.
    ok:                True if the turn completed a reply (agent didn't hard-fail).
    """

    reply_ts: str | None = None
    artifact_present: bool = False
    ok: bool = False


def filename_for(task_text: str) -> str:
    """Derive an artifact filename from the first request, e.g.
    "create a simple html snake game" → "snake-game.html". Falls back to
    "index.html". Heuristic + deterministic (good for a demo)."""
    import re as _re
    low = (task_text or "").lower()
    # Pull a short noun-ish slug: strip common request verbs/filler, keep 2-3 words.
    low = _re.sub(r"\b(can you|could you|please|create|build|make|me|a|an|the|simple|"
                  r"html|based|for|with|of|game|app|page|website|site|widget|tool)\b", " ", low)
    words = [w for w in _re.split(r"[^a-z0-9]+", low) if w]
    slug = "-".join(words[:3]).strip("-")
    if not slug:
        # nothing distinctive left — fall back to a generic name, but try to keep
        # a hint of the request (e.g. "snake" from "snake game").
        raw = [w for w in _re.split(r"[^a-z0-9]+", (task_text or "").lower()) if w]
        slug = "-".join(raw[:2]).strip("-") or "index"
    return f"{slug[:40]}.html"


async def _publish_html_diff(
    client: AsyncWebClient, logger: Logger, channel_id: str, *,
    prev_html: str, new_html: str, filename: str, branch: str,
) -> None:
    """Publish the Code (diff) + Preview (html) tabs for a regenerated artifact,
    SEQUENTIALLY (they share the channel's single agent_session_views array;
    concurrent setView calls race). The diff is incremental vs prev_html (full
    all-additions when prev_html==""). Both are best-effort — a failure is logged
    + audited via _report_view_results, never fatal."""
    diff = unified_diff(prev_html, new_html, filename)
    reported: list[tuple[str, object]] = []
    if diff:
        diff_r = await slackcode.set_view(
            client, channel_id,
            view_type="diff", content=diff, base_branch="main", head_branch=branch,
        )
        reported.append(("diff", diff_r))
    # Preview: reuse the timeout/fallback ladder via a tiny shim scenario-free —
    # publish the html string directly with the same 15s bound.
    try:
        prev_r = await asyncio.wait_for(
            slackcode.set_view(
                client, channel_id,
                view_type="html", view_key="preview", name="Preview", content=new_html,
            ),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        prev_r = slackcode.SlackCodeResult(ok=False, error="timeout(15s)")
    reported.append(("preview(html)", prev_r))
    await _report_view_results(client, logger, channel_id, reported)


async def run_artifact_turn(
    *,
    client: AsyncWebClient,
    logger: Logger,
    cfg: slackcode.SlackCodeConfig,
    channel_id: str,
    task_text: str,
    first_turn: bool,
    seed_html: str | None = None,
    seed_filename: str | None = None,
    branch: str = "master",
    user_id: str | None = None,
) -> ArtifactTurnResult:
    """Run one live-artifact turn in a code channel.

    The agent regenerates the COMPLETE artifact (```html``` fence); we diff it
    against the stored version and re-publish the Code + Preview tabs, store the
    new version, and stream the prose reply (with the Thinking plan block).

    Every externally-visible step is guarded by the stop registry: if the session
    was stopped (agent_session_stopped), we return immediately and make NO further
    setView / setStatus / chat.* calls for this context (Slack owns the status).

    Returns the ts of the streamed reply (for archive), or None.

    ``seed_html``/``seed_filename`` (first turn of a seeded scenario) prime the
    stored artifact so the agent edits an existing file rather than building from
    scratch.
    """

    def _stopped() -> bool:
        if slackcode.is_session_stopped(channel_id):
            logger.info("artifact turn: session stopped for %s — ceasing", channel_id)
            return True
        return False

    def _artifact_present() -> bool:
        return slackcode.get_channel_artifact(channel_id) is not None

    if _stopped():
        return ArtifactTurnResult(reply_ts=None, artifact_present=_artifact_present(), ok=False)

    # A seeded first turn (e.g. website_redesign) is a DISPLAY, not a rebuild: the
    # trigger ("implement the homepage redesign") isn't an edit request. Publish
    # the seed page to the Code + Preview tabs and store it BEFORE the agent runs
    # (so it appears instantly), then ask the agent to NARRATE the redesign (no
    # ```html``` regeneration). The user's later edits diff against this seed.
    # Freeform first turns (no seed) and all follow-ups take the build/edit path.
    seeded_display = bool(first_turn and seed_html and seed_filename)
    if seeded_display:
        # Store the seed now (so `prev` below sees it), but PUBLISH the diff/preview
        # only AFTER the Thinking spinner opens (below) — otherwise the diff appears
        # before the spinner and reads out of order (Thinking should come first, then
        # the diff it represents). The publish moved into the try block.
        slackcode.set_channel_artifact(channel_id, filename=seed_filename, html=seed_html)

    prev = slackcode.get_channel_artifact(channel_id)
    # branch is passed by the caller (default "master", matching the screenshots'
    # `Branch master`; a seeded scenario may pass its own feat/ branch).

    # Re-check the stop flag: the seeded-display _publish_html_diff above awaits
    # (yields to the loop), so a stop could have landed between it and here — and
    # after a stop we must issue NO setStatus.
    if _stopped():
        return ArtifactTurnResult(reply_ts=None, artifact_present=_artifact_present(), ok=False)
    await slackcode.set_session_status(client, channel_id, "processing")

    # From here on, wrap the working body so the session status ALWAYS returns to
    # `active` on exit — unless the session was stopped, in which case Slack owns
    # the status per the lifecycle spec (we must not call agents.sessions.* after
    # a stop). This is what prevents the "Stop agent" spinner from spinning
    # forever when a turn exits by an unexpected path.
    reply_ts: str | None = None
    streamer: _TopLevelStreamer | None = None
    thinking_plan: dict | None = None
    try:
        # Open the "Thinking" stream NOW — steps in the spinner state — BEFORE the
        # agent runs, so the loading indicator reflects real in-flight work rather
        # than a post-hoc replay. Finalized (steps → checkmarks, stream closed)
        # only after the work is done. We keep the SAME plan (thinking_plan) to
        # complete at the end so the spinner cards flip in place by matching ids.
        # If the stream can't open we fall back to a plain post at the end
        # (streamer stays None).
        _opened = await open_thinking_stream(
            client=client, logger=logger, channel_id=channel_id, recipient_user_id=user_id,
        )
        if _opened is not None:
            streamer, thinking_plan = _opened

        # SEEDED DISPLAY (website turn-0): publish the pre-built seed page NOW — after
        # the Thinking spinner is up, so the order reads Thinking → diff (not diff →
        # Thinking, which looked out of order). The page content is the static seed
        # (already stored above), so this doesn't depend on the agent; the agent then
        # just narrates it. Stop-guarded.
        if seeded_display and not _stopped():
            await _publish_html_diff(
                client, logger, channel_id,
                prev_html="", new_html=seed_html, filename=seed_filename, branch=branch,
            )

        # Build the agent input. Three shapes:
        #   • seeded first turn → narrate the already-published page (no html).
        #   • follow-up with a stored file → edit THAT file, return the whole thing.
        #   • freeform first turn → build from scratch, return the whole file.
        parts: list[str] = []
        if seeded_display:
            parts.append(
                f"The `{prev['filename']}` page is already built and showing in the Code and "
                "Preview tabs of this channel. Give a short, in-character narrative of the "
                "redesign you shipped (lead with a ```taskplan``` 'Thinking' checklist of the work, "
                "then 1–2 plain-text lines). Do NOT emit an ```html``` block — the page is already "
                "published; only regenerate it when the user asks for a change."
            )
        elif prev:
            parts.append(
                f"The current file is `{prev['filename']}`. Here it is in full:\n\n"
                f"```html\n{prev['html']}\n```\n\n"
                "Apply the requested change and return the COMPLETE updated file."
            )
        parts.append(f"Request: {task_text}")
        agent_input = "\n\n".join(parts)

        deps = AgentDeps(
            client=client, user_id=user_id or "", channel_id=channel_id,
            thread_ts=None, message_ts="", user_token=None,
        )
        user_display_name = await resolve_user_name(client, user_id) if user_id else None

        if _stopped():
            return ArtifactTurnResult(reply_ts=None, artifact_present=_artifact_present(), ok=False)
        existing_session_id = session_store.get_session(channel_id, "")
        response_text, new_session_id = "", None
        try:
            # Freeform / edit path: the diff IS the agent's generated HTML, so there's
            # nothing pre-built to show early — the wait is the real build. We DON'T
            # cap it (a cap can only produce a scaffold, which then can't heal to the
            # real file without a second prompt). Instead the "Thinking" spinner stays
            # up the whole time showing the working steps (_WORKING_PLAN: read → wrote
            # the file → refreshed the tabs) so it reads as WORKING, not hung, and
            # settles when the diff lands (finalize_thinking_stream below). Only the
            # overall RUN_AGENT_TIMEOUT (agent.py, default 300s) bounds a true hang.
            # Scripted stories — the demo heroes — publish their pre-built diff BEFORE
            # the agent (see _run_session_body) and are unaffected by this wait.
            response_text, new_session_id = await run_agent_offloop(
                agent_input, session_id=existing_session_id, deps=deps,
                user_display_name=user_display_name, with_tools=False,
            )
        except Exception as e:
            logger.warning("run_agent failed in artifact turn (%s)", type(e).__name__, exc_info=True)
            if not _stopped():
                await _close_stream_with_text(
                    streamer, client, channel_id, "I hit an error working that. Try again?",
                )
            return ArtifactTurnResult(reply_ts=None, artifact_present=_artifact_present(), ok=False)

        if new_session_id:
            session_store.set_session(channel_id, "", new_session_id)

        if _stopped():
            return ArtifactTurnResult(reply_ts=None, artifact_present=_artifact_present(), ok=False)

        # The agent can "succeed" (no exception) yet return nothing useful — e.g.
        # the host CLI printed a 401 to the channel and handed back an empty/blank
        # string. Treat an empty reply as a soft failure: close the stream with a
        # plain "couldn't complete that" line and DON'T let the caller post a
        # "Built X" summary (there's no artifact).
        if not (response_text or "").strip():
            if not _stopped():
                await _close_stream_with_text(
                    streamer, client, channel_id,
                    "I couldn't complete that just now — mind trying again?",
                )
            return ArtifactTurnResult(reply_ts=None, artifact_present=_artifact_present(), ok=False)

        # Extract the regenerated file. If present, re-publish tabs + store it.
        # On a seeded DISPLAY turn we already published the seed and told the agent
        # not to emit html — if it did anyway, ignore it (turn 0 is a display, not
        # a rebuild) and just narrate; the seed stays the base for later edits.
        new_html, prose = extract_html(response_text)
        if new_html and not seeded_display:
            filename = (prev or {}).get("filename") or seed_filename or filename_for(task_text)
            if not _stopped():
                await _publish_html_diff(
                    client, logger, channel_id,
                    prev_html=(prev or {}).get("html", ""), new_html=new_html,
                    filename=filename, branch=branch,
                )
            slackcode.set_channel_artifact(channel_id, filename=filename, html=new_html)
        # Otherwise (plain Q&A, or a seeded display turn where we ignore any html)
        # the tabs are left as-is. `prose` already has any fence stripped by
        # extract_html; if there was no fence it equals response_text.

        # The message we finalize: the prose (fence-stripped). Fall back to the
        # whole reply only if extraction somehow left prose empty. Also strip any
        # agent-emitted ```taskplan``` fence — we own the single "Thinking" block
        # (settled below), so a second plan in the reply would just stack.
        reply_body = prose if prose and prose.strip() else response_text
        _agent_plan, reply_body = extract_plan(reply_body)
        if not (reply_body or "").strip():
            reply_body = "Done — take a look in the *Code* and *Preview* tabs."

        if _stopped():
            return ArtifactTurnResult(reply_ts=None, artifact_present=_artifact_present(), ok=False)

        # Settle the "Thinking" spinner to a bare `✓ Thinking` header (matching ids,
        # so the spinner cards flip in place) and close the stream — that clears the
        # loading indicator. Then post the reply as its OWN plain chat.postMessage,
        # so it appears fully-formed at once (no streamed "bubble appears, then the
        # content/spinner fills in a beat later" lag, and no second Thinking block
        # rendered inside the reply). The reply lands just below the settled Thinking.
        if streamer is not None and thinking_plan is not None:
            await finalize_thinking_header(streamer=streamer, plan=thinking_plan, logger=logger)
        try:
            posted = await client.chat_postMessage(channel=channel_id, markdown_text=reply_body)
            reply_ts = posted.get("ts")
        except Exception:
            logger.warning("artifact-turn reply post failed", exc_info=True)
            reply_ts = None
        return ArtifactTurnResult(reply_ts=reply_ts, artifact_present=_artifact_present(), ok=True)
    finally:
        # Close any still-open Thinking stream so its message-level loading
        # indicator never spins forever. This catches the mid-turn stop paths:
        # when agent_session_stopped lands while the agent is running, the three
        # _stopped() early-returns above exit WITHOUT finalizing the stream, so a
        # stopStream is still owed. Closing a chat stream is a chat.* call, NOT an
        # agents.* call, so it's allowed after a stop (the spec only bars
        # agents.sessions.*/agents.conversations.* — Slack owns the session STATUS,
        # but the streaming message is still ours to end). stop() is idempotent, so
        # on the normal success path (stream already finalized with the answer) this
        # is a no-op; best-effort, never raises.
        if streamer is not None and streamer.is_open:
            try:
                await streamer.stop(markdown_text="Stopped." if slackcode.is_session_stopped(channel_id) else "Done.")
            except Exception:  # noqa: BLE001 — closing the indicator is best-effort
                logger.warning("run_artifact_turn: closing stranded stream failed", exc_info=True)
        # Always return the session to idle — UNLESS it was stopped (then Slack
        # owns the status and we must not call agents.sessions.*). This guarantees
        # the "Stop agent" spinner resolves no matter which path we exit by.
        if not slackcode.is_session_stopped(channel_id):
            await slackcode.set_session_status(client, channel_id, "active")


async def _close_stream_with_text(
    streamer: _TopLevelStreamer | None, client: AsyncWebClient, channel_id: str, text: str,
) -> None:
    """Finalize an open thinking-stream with a plain message (error/empty paths),
    or post a normal message if no stream is open. Best-effort; never raises."""
    try:
        if streamer is not None and streamer.ts:
            await streamer.stop(markdown_text=text)
        else:
            await client.chat_postMessage(channel=channel_id, text=text)
    except Exception:  # noqa: BLE001 — the finally-block status reset still runs
        pass


async def run_view_probe(client: AsyncWebClient, logger: Logger, channel_id: str) -> list[str]:
    """Live-probe the confidential-beta setView/setProperties shapes and return a
    list of human-readable result lines (also logged).

    Callable two ways with the SAME code:
      • qa.py --probe (an external AsyncWebClient built from a pasted xoxb), or
      • from inside the running bot (its own already-authenticated client), via
        the code-channel keyword hook — which sidesteps token juggling entirely.

    Non-destructive-ish: setView upserts throwaway `probe_canvas_*` keys; the
    context-bar action item REPLACES the bar with one probe item (a normal turn
    repaints it). Every call is best-effort; a failure becomes a result line, not
    an exception."""
    lines: list[str] = []

    def _rec(label: str, r) -> None:
        msg = (f"[{'OK ' if getattr(r, 'ok', False) else 'ERR'}] {label}: "
               f"ok={getattr(r, 'ok', None)} error={getattr(r, 'error', None)!r} "
               f"data_keys={sorted((r.data or {}).keys()) if getattr(r, 'data', None) else None}")
        lines.append(msg)
        logger.warning("view-probe %s", msg)

    is_cc = await slackcode.is_code_channel(client, channel_id, use_cache=False)
    lines.append(f"target {channel_id} is_code_channel={is_cc}")
    logger.warning("view-probe target %s is_code_channel=%s", channel_id, is_cc)
    if not is_cc:
        lines.append("not a code channel — every agents.conversations.* call would 400.")
        return lines

    # --- Canvas (CONFIRMED flow): canvases.create -> setView(type=canvas, canvas_id).
    # A canvas view attaches an existing canvas by id; it does NOT take inline
    # content (every inline variant returned missing_required_arg because the
    # missing arg was canvas_id). publish_canvas_view does the two-step; this
    # proves the flow still works and leaves a throwaway probe canvas tab.
    _rec("canvas/publish_canvas_view(create+attach)",
         await slackcode.publish_canvas_view(
             client, channel_id, title="Probe Canvas",
             markdown="## Probe\nConfirmed two-step canvas flow.",
             view_key="probe_canvas", name="Probe Canvas", access_level="comment"))

    # --- Interactive context-bar item (click it, read the inbound event) ---
    _rec("setProperties/context_bar_item_type=action",
         await slackcode.set_properties(
             client, channel_id,
             context_bar_items=[{"key": "probe-action", "label": "Probe: click me",
                                 "icon": "terminal", "item_type": "action"}]))

    # --- listViews (what landed + envelope shape) ---
    lv = await slackcode.list_views(client, channel_id)
    _rec("listViews", lv)
    if lv.ok and lv.data:
        lines.append(f"listViews raw data: {lv.data}")
        logger.warning("view-probe listViews raw: %s", lv.data)
    return lines


async def _post_artifact_summary_and_archive(
    client: AsyncWebClient, logger: Logger, channel_id: str, *, branch: str, reply_ts: str | None,
) -> None:
    """Post the session's light first-pass summary (a couple of bullets of what
    changed + a review nudge). Uses the stored artifact's filename so it names the
    real file. Stop-guarded by the caller; every call here is best-effort.

    NOTE: this deliberately does NOT post an "Archive channel" button. Offering to
    archive on the very first reply is premature — the work has barely started. A
    person can archive whenever they want, and the scripted resolution flow
    (/recap etc.) still offers an archive button once an issue is actually resolved.
    """
    if slackcode.is_session_stopped(channel_id):
        return
    art = slackcode.get_channel_artifact(channel_id)
    fname = (art or {}).get("filename") or "the file"
    try:
        summary = await client.chat_postMessage(
            channel=channel_id,
            markdown_text=(
                f":white_check_mark: First pass up on `{branch}`.\n"
                f"• Built `{fname}` — it's in the *Code* tab (diff) and rendered in the *Preview* tab.\n"
                f"• Tell me what to change — e.g. _\"make the background darker\"_ — and I'll update both.\n"
                "_AI-generated; give it a review before you ship it._"
            ),
        )
    except Exception:
        logger.warning("artifact summary post failed", exc_info=True)
        return
    summary_ts = summary.get("ts") or reply_ts
    if slackcode.is_session_stopped(channel_id):
        return
    # Mark the summary discoverable while live (see the scripted path). Best-effort.
    if summary_ts and not slackcode.is_session_stopped(channel_id):
        await slackcode.set_properties(
            client, channel_id, summary_message={"message_ts": summary_ts},
        )
