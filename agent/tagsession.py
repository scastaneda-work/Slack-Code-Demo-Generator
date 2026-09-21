"""Claude Tag — per-thread checklist state + the pure render/mutation helpers.

The in-thread Tag surface (see listeners/events/slack_tag.py) posts ONE checklist
message per thread with chat.postMessage and then re-updates it in place with
chat.update as the checklist auto-advances (and when a human correction re-plans it).
This module owns:

  • the per-(channel, thread) registry of {slug, checklist_ts, plan, turn}, durable
    across a restart via an atomic temp-file+rename write (mirrors the Slack Code
    state store, but a SEPARATE file so the two never entangle);
  • the PURE helpers the orchestrator and the offline QA both use — rendering a plan
    dict to the checklist blocks/text ("✓ / ✱ / ○" + "todos as of <time>"), applying a
    scenario turn-delta to a plan, and building the chat.postMessage / chat.update
    payloads. Keeping these pure + Slack-free is what lets qa.py --self-test cover the
    checklist behavior without a live workspace.

The checklist plan is the SAME dict shape as agent.taskplan (mode:"plan", steps with
id/title/status), so taskplan.validate_plan can validate scenario plans for free. We
do NOT use the taskplan STREAM renderer (agent.render) — a streamed message is
start→append→stop and not re-editable across turns; a plain message we own + chat.update
is the only deterministic cross-turn update path.
"""
from __future__ import annotations

import copy
import json as _json
import os
from datetime import datetime

# Status glyphs (match the product screenshots).
_GLYPH = {"complete": "✓", "in_progress": "✱", "pending": "○", "error": "⚠"}


# ---------------------------------------------------------------------------
# Durable per-thread registry
# ---------------------------------------------------------------------------
# Keyed by "<channel_id>\t<thread_ts>". DURABLE (atomic write) so a checklist opened by
# one process can still be re-updated after a relaunch — though a restart DROPS any
# in-flight auto-advance loop (in-process only; the orchestrator re-derives state from
# the stored plan on the next human follow-up). Separate file from Slack Code's state.
_STATE_PATH = os.environ.get("SLACK_TAG_STATE_PATH") or os.path.join(os.getcwd(), ".tag_state.json")


def _load_state() -> dict:
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — missing/corrupt → start empty
        return {}


_TAG_THREADS: dict[str, dict] = dict(_load_state().get("threads", {}))


def _save_state() -> None:
    """Persist the registry atomically; best-effort (a write failure never breaks a reply)."""
    try:
        tmp = f"{_STATE_PATH}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump({"threads": _TAG_THREADS}, f)
        os.replace(tmp, _STATE_PATH)
    except Exception:  # noqa: BLE001 — persistence is best-effort
        pass


def _key(channel_id: str, thread_ts: str) -> str:
    return f"{channel_id}\t{thread_ts}"


def set_tag_thread(channel_id: str, thread_ts: str, state: dict) -> None:
    """Record/replace the Tag checklist state for a thread.
    ``state`` = {"slug", "checklist_ts", "plan", "turn", "done"?}."""
    if channel_id and thread_ts:
        _TAG_THREADS[_key(channel_id, thread_ts)] = state
        _save_state()


def get_tag_thread(channel_id: str, thread_ts: str) -> dict | None:
    """Return the Tag checklist state for a thread, or None if this thread isn't a
    Tag thread."""
    return _TAG_THREADS.get(_key(channel_id, thread_ts))


def clear_tag_thread(channel_id: str, thread_ts: str) -> None:
    _TAG_THREADS.pop(_key(channel_id, thread_ts), None)
    _save_state()


def is_tag_thread(channel_id: str, thread_ts: str) -> bool:
    """True if Claude Tag is running a checklist in this thread (so a non-mention
    follow-up should be routed to the Tag follow-up handler)."""
    return _key(channel_id, thread_ts) in _TAG_THREADS


# ---------------------------------------------------------------------------
# Pure plan helpers (Slack-free — unit-tested offline)
# ---------------------------------------------------------------------------
def start_plan(initial: dict) -> dict:
    """Return a deep copy of a scenario's initial_plan() with step 0 flipped to
    in_progress (the rest left pending) — the turn-0 posted state."""
    plan = copy.deepcopy(initial)
    steps = plan.get("steps", [])
    if steps:
        steps[0]["status"] = "in_progress"
    return plan


def next_pending_index(plan: dict) -> int:
    """Index of the first step that is not complete, or -1 if all complete."""
    for i, s in enumerate(plan.get("steps", [])):
        if s.get("status") != "complete":
            return i
    return -1


def advance_plan(plan: dict) -> tuple[dict, bool]:
    """Advance the checklist ONE tick: mark the current in_progress step complete and
    the next pending step in_progress. Returns (new_plan, all_done). Pure — the
    auto-advance loop calls this between dwells."""
    plan = copy.deepcopy(plan)
    steps = plan.get("steps", [])
    # Complete the first in_progress step (or, if none, the first pending — defensive).
    cur = next((i for i, s in enumerate(steps) if s.get("status") == "in_progress"), None)
    if cur is None:
        cur = next((i for i, s in enumerate(steps) if s.get("status") == "pending"), None)
    if cur is not None:
        steps[cur]["status"] = "complete"
    # Start the next not-yet-complete step, if any.
    nxt = next((i for i, s in enumerate(steps) if s.get("status") == "pending"), None)
    if nxt is not None:
        steps[nxt]["status"] = "in_progress"
    all_done = all(s.get("status") == "complete" for s in steps) if steps else True
    return plan, all_done


def apply_delta(plan: dict, delta: dict) -> dict:
    """Apply a scenario turn-delta (status flips by id + an optional inserted step) to
    a plan. Pure. Unknown step ids in `set` are ignored (defensive). An `insert` adds
    a step after the named id (or at the end if not found)."""
    plan = copy.deepcopy(plan)
    steps = plan.get("steps", [])
    by_id = {s["id"]: s for s in steps}
    for sid, status in (delta.get("set") or {}).items():
        if sid in by_id and status in _GLYPH:
            by_id[sid]["status"] = status
    ins = delta.get("insert")
    if ins and ins.get("id") and ins["id"] not in by_id:
        new_step = {"id": ins["id"], "title": ins.get("title", ""),
                    "status": ins.get("status", "in_progress")}
        after = ins.get("after")
        pos = next((i for i, s in enumerate(steps) if s["id"] == after), len(steps) - 1)
        steps.insert(pos + 1, new_step)
    return plan


def _now_hhmm(now: datetime | None = None) -> str:
    """'3:51 PM' style time for the 'todos as of' footer. `now` injectable for tests."""
    now = now or datetime.now()
    # %-I is platform-specific; strip a leading zero portably.
    return now.strftime("%I:%M %p").lstrip("0")


def render_checklist_text(plan: dict, *, now: datetime | None = None) -> str:
    """Render a plan dict to the mrkdwn body of the checklist message: a bold title,
    one '<glyph> <title>' line per step, and an italic 'todos as of <time>' footer.
    Slack mrkdwn = single-asterisk bold."""
    lines: list[str] = []
    title = plan.get("title")
    if title:
        lines.append(f"*{title}*")
    for s in plan.get("steps", []):
        glyph = _GLYPH.get(s.get("status", "pending"), "○")
        lines.append(f"{glyph} {s.get('title', '')}")
    lines.append("")
    lines.append(f"_todos as of {_now_hhmm(now)}_")
    return "\n".join(lines)


def checklist_blocks(plan: dict, *, open_in_claude_url: str | None = None,
                     now: datetime | None = None) -> list[dict]:
    """The checklist as Block Kit: one section with the rendered body, plus an optional
    context row carrying the inert 'Open session in Claude' link (a visual prop —
    Slack has no deep-link that runs a session)."""
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": render_checklist_text(plan, now=now)}},
    ]
    if open_in_claude_url:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"<{open_in_claude_url}|Open session in Claude>"}],
        })
    return blocks


def post_payload(channel_id: str, thread_ts: str, plan: dict, *,
                 open_in_claude_url: str | None = None, now: datetime | None = None) -> dict:
    """kwargs for the turn-0 chat.postMessage (threaded). `text` is the required
    fallback for a blocks message."""
    return {
        "channel": channel_id,
        "thread_ts": thread_ts,
        "blocks": checklist_blocks(plan, open_in_claude_url=open_in_claude_url, now=now),
        "text": "todos",
    }


def update_payload(channel_id: str, checklist_ts: str, plan: dict, *,
                   open_in_claude_url: str | None = None, now: datetime | None = None) -> dict:
    """kwargs for a chat.update re-rendering the SAME checklist message in place."""
    return {
        "channel": channel_id,
        "ts": checklist_ts,
        "blocks": checklist_blocks(plan, open_in_claude_url=open_in_claude_url, now=now),
        "text": "todos",
    }
