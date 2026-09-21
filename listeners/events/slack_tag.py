"""Claude Tag — in-thread lightweight collaboration orchestration.

The lightweight counterpart to Slack Code channels: when @Claude is tagged in a
channel THREAD for a task a Tag scenario claims (scenarios_tag.route_tag_scenario),
this runs the whole beat IN THE THREAD — no channel spun up:

    "On it — …" (commitment)
      → post a live TODO checklist message (chat.postMessage, threaded)
      → AUTO-ADVANCE it on a timer (chat.update the SAME message, ○→✱→✓)
      → a human correction INTERRUPTS: replan (insert a step) + re-tick
      → close with a scripted "✅ Up: #… " PR line (or a plain "Done —" for a doc task)

The checklist is a plain message we own + chat.update — NOT a taskplan stream (a
stream is start→append→stop and not re-editable across turns). Pure render/mutation
helpers live in agent/tagsession.py (unit-tested offline); this module is the async
Slack I/O + the auto-advance lifecycle. All artifacts are FAKE demo props.

Concurrency invariant: exactly ONE writer to a given checklist message at a time. A
per-thread asyncio.Lock guards every chat.update; the auto-advance runs as a task we
cancel before a follow-up writes, then relaunch — so the timer and a correction never
race on the same message ts.
"""
from __future__ import annotations

import asyncio
import os
from logging import Logger

from slack_sdk.web.async_client import AsyncWebClient

from agent import tagsession
from agent.audit import audit_log
import random

from scenarios_tag import load_tag_scenario

# Per-step dwell for the auto-advance (seconds). By DEFAULT each step waits a RANDOM
# 3–5s, so the checklist ticks at an uneven, human-looking pace rather than a robotic
# fixed interval. Setting SLACK_TAG_STEP_DWELL_S pins a FIXED dwell instead (any
# non-negative float): demos that want a specific cadence use it, and the offline QA
# harness sets it to 0 so tests don't actually sleep.
_STEP_DWELL_MIN_S = 3.0
_STEP_DWELL_MAX_S = 5.0


def _step_dwell_s() -> float:
    """Seconds to wait before the next checklist tick. Random 3–5s per call unless
    SLACK_TAG_STEP_DWELL_S pins a fixed value."""
    raw = os.environ.get("SLACK_TAG_STEP_DWELL_S", "")
    if raw:
        try:
            v = float(raw)
            if v >= 0:
                return v
        except ValueError:
            pass
    return random.uniform(_STEP_DWELL_MIN_S, _STEP_DWELL_MAX_S)


# Per-thread coordination: one writer-lock + the running auto-advance task, keyed
# (channel_id, thread_ts). In-process only (a restart drops these along with any
# in-flight loop — acceptable for a demo; the registry in tagsession is what persists).
_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_ADVANCE_TASKS: dict[tuple[str, str], asyncio.Task] = {}


def _lock(channel_id: str, thread_ts: str) -> asyncio.Lock:
    key = (channel_id, thread_ts)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


async def _cancel_advance(channel_id: str, thread_ts: str) -> None:
    """Cancel a running auto-advance task for this thread and await its unwind, so the
    follow-up path becomes the sole writer before it touches the checklist."""
    task = _ADVANCE_TASKS.pop((channel_id, thread_ts), None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 — unwind only
            pass


async def _finish(client: AsyncWebClient, logger: Logger, channel_id: str,
                  thread_ts: str, scenario) -> None:
    """Post the closing line (scripted PR line, or a plain 'Done —' for a no-PR task)
    and mark the thread done. Called once the checklist is all-complete."""
    try:
        line = scenario.result_line()
    except Exception:  # noqa: BLE001
        line = None
    if not line:
        title = (scenario.initial_plan() or {}).get("title") or "the task"
        line = f":white_check_mark: Done — {title.lower()}."
    # Guard against a double-post: `done` is set under the lock in _auto_advance's
    # final tick; only the FIRST caller to see (done and not finish_posted) posts the
    # closing line. (A re-opened-then-refinished checklist finishes once more.)
    state = tagsession.get_tag_thread(channel_id, thread_ts)
    if state is not None:
        if state.get("finish_posted"):
            return
        state["finish_posted"] = True
        tagsession.set_tag_thread(channel_id, thread_ts, state)
    try:
        await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, markdown_text=line)
    except Exception:  # noqa: BLE001
        logger.warning("tag finish line post failed in %s", channel_id, exc_info=True)
    await audit_log(
        client,
        f":checkered_flag: Claude Tag finished in <#{channel_id}> thread `{thread_ts}` "
        f"(scenario={scenario.SLUG}).",
    )


async def _auto_advance(client: AsyncWebClient, logger: Logger, channel_id: str,
                        thread_ts: str, scenario) -> None:
    """Tick the checklist forward on a timer until every step is complete, then finish.
    Each tick: dwell → advance_plan → chat.update the SAME message (under the writer
    lock) → persist. Cancellable: a follow-up cancels this before it re-plans, and it
    self-aborts if the thread state disappears. Never raises out (best-effort)."""
    try:
        while True:
            await asyncio.sleep(_step_dwell_s())
            state = tagsession.get_tag_thread(channel_id, thread_ts)
            if state is None or state.get("done"):
                return
            finished_now = False
            async with _lock(channel_id, thread_ts):
                # Re-read inside the lock (a follow-up may have mutated the plan).
                state = tagsession.get_tag_thread(channel_id, thread_ts)
                if state is None or state.get("done"):
                    return
                new_plan, all_done = tagsession.advance_plan(state["plan"])
                state["plan"] = new_plan
                state["turn"] = state.get("turn", 0) + 1
                # Mark done ATOMICALLY with the final tick, INSIDE the lock. This
                # closes the race where a follow-up arriving right at completion could
                # slip between the last update and `done` being set (and either lose
                # its replan or find a half-finished state). Once `done` is set here,
                # any follow-up that still wants to re-open must do so under the lock.
                if all_done:
                    state["done"] = True
                tagsession.set_tag_thread(channel_id, thread_ts, state)
                try:
                    await client.chat_update(**tagsession.update_payload(
                        channel_id, state["checklist_ts"], new_plan,
                        open_in_claude_url=getattr(scenario, "OPEN_IN_CLAUDE_URL", None),
                    ))
                except Exception:  # noqa: BLE001 — a failed tick shouldn't kill the loop
                    logger.warning("tag auto-advance chat.update failed in %s", channel_id, exc_info=True)
                finished_now = all_done
            if finished_now:
                await _finish(client, logger, channel_id, thread_ts, scenario)
                return
    except asyncio.CancelledError:
        raise  # a follow-up cancelled us; unwind cleanly (it will relaunch)


def _launch_advance(client: AsyncWebClient, logger: Logger, channel_id: str,
                    thread_ts: str, scenario) -> None:
    """Start (or restart) the auto-advance task for this thread."""
    key = (channel_id, thread_ts)
    old = _ADVANCE_TASKS.get(key)
    if old and not old.done():
        return  # already advancing
    _ADVANCE_TASKS[key] = asyncio.create_task(
        _auto_advance(client, logger, channel_id, thread_ts, scenario)
    )


async def run_tag_session(
    *, client: AsyncWebClient, logger: Logger, channel_id: str, thread_ts: str,
    slug: str, user_id: str | None = None,
) -> bool:
    """Turn 0: open a Tag checklist in the thread and start it auto-advancing.

    Posts the commitment line, posts the checklist message (step 0 in_progress),
    records the thread state, fires the scenario's opening chime-ins, then launches
    the auto-advance loop. Returns True if the checklist was posted (so the caller
    stops routing), False if the scenario couldn't load (caller falls through)."""
    scenario = load_tag_scenario(slug)
    if scenario is None:
        return False

    # Commitment line (Claude's own voice), threaded.
    try:
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts,
            markdown_text=getattr(scenario, "COMMITMENT", "On it."),
        )
    except Exception:  # noqa: BLE001 — best-effort; keep going to the checklist
        logger.warning("tag commitment post failed in %s", channel_id, exc_info=True)

    plan = tagsession.start_plan(scenario.initial_plan())
    open_url = getattr(scenario, "OPEN_IN_CLAUDE_URL", None)
    try:
        resp = await client.chat_postMessage(
            **tagsession.post_payload(channel_id, thread_ts, plan, open_in_claude_url=open_url)
        )
    except Exception:  # noqa: BLE001 — no checklist ts → we can't run the beat
        logger.warning("tag checklist post failed in %s — aborting Tag turn", channel_id, exc_info=True)
        return False
    checklist_ts = resp.get("ts")
    if not checklist_ts:
        return False

    tagsession.set_tag_thread(channel_id, thread_ts, {
        "slug": slug, "checklist_ts": checklist_ts, "plan": plan, "turn": 0, "done": False,
    })
    await audit_log(
        client,
        f":sparkles: Claude Tag opened in <#{channel_id}> thread `{thread_ts}` "
        f"(scenario={slug}) for ~{await _display(client, user_id)}.",
    )

    # Opening chime-in(s): any beat authored with an "arrival"/turn-0 flavor. We reuse
    # the FIRST turn-delta's beat as the arrival voice only if it has phase "arrival";
    # otherwise the cast stays quiet until they actually correct course (screenshot-true).
    # (Kept intentionally minimal — "less is more".)

    _launch_advance(client, logger, channel_id, thread_ts, scenario)
    return True


async def run_tag_followup(
    *, client: AsyncWebClient, logger: Logger, channel_id: str, thread_ts: str,
    text: str, user_id: str | None = None,
) -> None:
    """A human posted in an active Tag thread — INTERRUPT the auto-advance and re-plan.

    Cancels the running tick task, applies the next unused scenario turn-delta (status
    flips + an optional inserted 'Replanning — …' step), chat.updates the checklist once,
    then relaunches the auto-advance from the new state. All under the one-writer lock.
    Does NOT spoof a human chime-in — the correction voice is the real teammate who
    posted (Claude only spoofs personas in a dedicated code channel, never in a thread)."""
    state = tagsession.get_tag_thread(channel_id, thread_ts)
    if state is None:
        return
    scenario = load_tag_scenario(state.get("slug"))
    if scenario is None:
        return

    # Become the sole writer before touching the checklist.
    await _cancel_advance(channel_id, thread_ts)

    reopened = False
    async with _lock(channel_id, thread_ts):
        state = tagsession.get_tag_thread(channel_id, thread_ts)
        if state is None:
            return
        # Pick the next unused delta whose trigger matches this follow-up.
        deltas = []
        try:
            deltas = scenario.turn_deltas() or []
        except Exception:  # noqa: BLE001
            deltas = []
        used = state.get("deltas_used") or 0
        low = (text or "").lower()
        delta = None
        idx = used
        while idx < len(deltas):
            d = deltas[idx]
            trig = d.get("trigger", "any")
            if trig == "any" or trig in low:
                delta = d
                idx += 1
                break
            idx += 1
        # A matching correction is honored EVEN IF the checklist just finished — a
        # human's steer should always land, including one that arrives right at the
        # completion boundary (the race we fixed). Re-open the checklist: clear `done`,
        # apply the delta, and relaunch the auto-advance so the remaining/new steps
        # tick again. If the thread is done and there's NO matching delta, there's
        # nothing to do (don't re-open on an unrelated late message).
        if state.get("done") and delta is None:
            return
        if state.get("done") and delta is not None:
            state["done"] = False
            reopened = True
        if delta is None:
            # No scripted correction left/matching — nudge and resume advancing.
            state["deltas_used"] = used
            tagsession.set_tag_thread(channel_id, thread_ts, state)
        else:
            new_plan = tagsession.apply_delta(state["plan"], delta)
            state["plan"] = new_plan
            state["deltas_used"] = idx
            tagsession.set_tag_thread(channel_id, thread_ts, state)
            # NOTE: we deliberately do NOT spoof a scripted human chime-in here. In a
            # normal channel thread the correction comes from a REAL teammate (their own
            # user token); Claude AI only ever spoofs personas inside a dedicated CODE
            # channel, not in an ordinary thread. So Tag just re-plans the checklist —
            # the human voice is the actual person who posted. (A scenario delta's
            # optional `beat` field is unused on the Tag path for this reason.)
            try:
                await client.chat_update(**tagsession.update_payload(
                    channel_id, state["checklist_ts"], new_plan,
                    open_in_claude_url=getattr(scenario, "OPEN_IN_CLAUDE_URL", None),
                ))
            except Exception:  # noqa: BLE001
                logger.warning("tag followup chat.update failed in %s", channel_id, exc_info=True)
            await audit_log(
                client,
                f":arrows_counterclockwise: Claude Tag replanned in <#{channel_id}> thread "
                f"`{thread_ts}` (scenario={scenario.SLUG}).",
            )

    # Resume the auto-advance from the (possibly re-planned) state.
    _launch_advance(client, logger, channel_id, thread_ts, scenario)


async def _display(client: AsyncWebClient, user_id: str | None) -> str:
    """Best-effort display name for the audit line (falls back to the raw id)."""
    if not user_id:
        return "someone"
    try:
        from agent.identity import resolve_user_name
        return await resolve_user_name(client, user_id) or user_id
    except Exception:  # noqa: BLE001
        return user_id
