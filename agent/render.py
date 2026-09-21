"""Render a parsed taskplan (see taskplan.py) onto a Slack chat stream as the
animated "loading" experience that precedes the final answer.

Two display modes, both driven by the same step list:
  - "timeline": each step is an individual task card (the "Searched weather.com…
    → Found data from 2 sources" look). Source links render as a "Sources" row.
  - "plan": a single grouped plan block with a title and a checklist of tasks
    (the "Thinking Completed → ✓ Fetched → ✓ Checked" look).

Slack updates a task card in place when a later chunk reuses the same `id`. We
lean on that to animate pending → in_progress → complete. The docs don't put
that contract in writing, so the two-phase spinner is gated behind ANIMATE_STEPS
— if live testing ever shows duplicate cards, flip it to False for a single
terminal chunk per step with zero behavior risk.
"""

import asyncio
from urllib.parse import urlparse

from slack_sdk.models.blocks.block_elements import UrlSourceElement
from slack_sdk.models.messages.chunk import PlanUpdateChunk, TaskUpdateChunk

# Seconds the "in_progress" spinner shows before a step flips to complete.
# appendStream is Tier 4 (100+/min), so this cadence is well within limits.
STEP_PACE_S = 0.8
# Two-phase (in_progress → complete) animation per step. See module docstring.
ANIMATE_STEPS = True


def _display_mode(plan: dict) -> str:
    return "plan" if plan.get("mode") == "plan" else "timeline"


def _sources(step: dict) -> list[UrlSourceElement] | None:
    raw = step.get("sources")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[UrlSourceElement] = []
    for src in raw:
        if not isinstance(src, dict):
            continue
        url = src.get("url")
        if not url:
            continue
        # UrlSourceElement requires both url and text; fall back to the hostname.
        text = src.get("text") or urlparse(url).netloc or url
        out.append(UrlSourceElement(url=url, text=text))
    return out or None


def _task_chunk(step: dict, status: str, *, terminal: bool) -> TaskUpdateChunk:
    """Build a task_update chunk. Detail fields and sources are only attached on
    the terminal (final) chunk so the in_progress spinner stays clean."""
    return TaskUpdateChunk(
        id=step["id"],
        title=step["title"],
        status=status,
        details=step.get("details") if terminal else None,
        output=step.get("output") if terminal else None,
        sources=_sources(step) if terminal else None,
    )


def _final_status(step: dict) -> str:
    st = step.get("status")
    return st if st in {"complete", "error"} else "complete"


async def render_plan_steps_pending(streamer, plan: dict) -> None:
    """Phase 1 of a two-phase render: show the plan title and every step in the
    `in_progress` (spinner) state, and RETURN — no sleeps, no terminal flip.

    Use this to open the "Thinking" block BEFORE the real work runs, so the
    spinner reflects genuine in-flight work rather than a cosmetic timer. Pair
    with `render_plan_steps_complete` once the work finishes. The stream stays
    open (the caller closes it with `streamer.stop(...)`), so Slack keeps the
    message-level loading indicator up the whole time the agent is working.

    An `error` step is shown terminally right away (there's nothing to wait for).
    """
    if _display_mode(plan) == "plan" and plan.get("title"):
        await streamer.append(chunks=[PlanUpdateChunk(title=plan["title"])])

    for step in plan.get("steps", []):
        if _final_status(step) == "error":
            await streamer.append(chunks=[_task_chunk(step, "error", terminal=True)])
        else:
            # Spinner only — no details/sources yet (that's the terminal chunk).
            await streamer.append(chunks=[_task_chunk(step, "in_progress", terminal=False)])


async def render_plan_steps_complete(streamer, plan: dict, *, pace_s: float = 0.0) -> None:
    """Phase 2 of a two-phase render: flip each step from its `in_progress`
    spinner to its terminal status (complete/error), attaching details/sources.

    Reusing each step's `id` updates the same card in place, so the spinner
    settles into a checkmark. `pace_s` optionally staggers the flips for a
    "steps finishing one by one" feel; default 0 finalizes them immediately
    (the work is already done by the time this runs). `error` steps were already
    shown terminally in phase 1, so they're skipped here.
    """
    for step in plan.get("steps", []):
        final = _final_status(step)
        if final == "error":
            continue  # already terminal from the pending phase
        await streamer.append(chunks=[_task_chunk(step, final, terminal=True)])
        if pace_s:
            await asyncio.sleep(pace_s)


async def render_plan(streamer, plan: dict, *, pace_s: float = STEP_PACE_S) -> None:
    """Single-shot plan render (spinner → settle per step), for callers that
    animate the plan AFTER the work is done — the DM/assistant path
    (`finalize.py`), where there's no separate in-flight window to bracket.

    Preserved byte-for-byte in behavior: each step shows the `in_progress`
    spinner, waits `pace_s`, then settles to its terminal state. For streaming
    the plan AROUND real work (code channels), use the two-phase
    `render_plan_steps_pending` / `render_plan_steps_complete` pair instead.
    """
    if _display_mode(plan) == "plan" and plan.get("title"):
        await streamer.append(chunks=[PlanUpdateChunk(title=plan["title"])])

    for step in plan.get("steps", []):
        final = _final_status(step)
        if ANIMATE_STEPS and final != "error":
            # Show the spinner first, then settle into the terminal state with
            # details/sources. Reusing the same id updates the card in place.
            await streamer.append(chunks=[_task_chunk(step, "in_progress", terminal=False)])
            await asyncio.sleep(pace_s)
            await streamer.append(chunks=[_task_chunk(step, final, terminal=True)])
        else:
            await streamer.append(chunks=[_task_chunk(step, final, terminal=True)])
            await asyncio.sleep(pace_s)
