"""Claude Tag *scenarios* — the scripted stories for the in-thread lightweight
collaboration surface (the counterpart to the heavy Slack Code channels).

A **Tag scenario** is one Python module holding everything a lightweight in-thread
task shows: the opening "On it…" commitment, the initial TODO checklist, the
per-turn deltas that tick the checklist forward (and insert "Replanning …" steps
when the group corrects course), the scripted human chime-ins, and the closing
result line (a PR-style "✅ Up: #… " line, or None for a non-code task).

This package is DELIBERATELY separate from `scenarios/` (the Slack Code stories) so
the two routers/loaders can never load each other's modules. Slack Code spins up a
dedicated channel with artifact tabs; Tag stays in the thread and drives a single
`chat.postMessage` checklist message via `chat.update` (see agent/tagsession.py and
listeners/events/slack_tag.py).

Contract every Tag scenario module must satisfy (duck-typed; see `_template.py`):
  SLUG: str                                   # module name + this must match
  KEYWORDS: tuple[str, ...]                    # what route_tag_scenario() claims
  COMMITMENT: str                              # the "On it — …" opening line
  def initial_plan() -> dict                   # taskplan-shaped {mode:"plan", title, steps:[...]}
                                               #   all steps start "pending" (the orchestrator
                                               #   flips step 0 to in_progress on the first post)
  def turn_deltas() -> list[dict]              # ordered per-follow-up mutations (see below)
  PARTICIPANTS: list[dict]                     # OPTIONAL — origin-thread humans ({name, email_stem, role})
  def result_line() -> str | None              # the closing "✅ Up: #… " line, or None (no PR)
  OPEN_IN_CLAUDE_URL: str                       # inert visual-prop deep link (Slack has no real one)

A **turn delta** (one per follow-up / correction) is a dict:
  {
    "trigger": "any" | "<substring>",    # "any" = fires on any follow-up at this index;
                                          #   a substring = only when the follow-up text contains it
    "set": {"<step_id>": "complete"|"in_progress"|"pending"|"error", ...},   # status flips by id
    "insert": {"after": "<step_id>", "id": "<new_id>", "title": "Replanning — …",
               "status": "in_progress"},   # OPTIONAL — a new step (the replan beat)
    "beat": {"name": "Nadia", "text": "should be workspace-level, not per-user"},  # OPTIONAL chime-in
  }

The AUTO-ADVANCE loop (agent/tagsession + slack_tag) walks the checklist forward on
a timer without any of these deltas; `turn_deltas()` only fire when a HUMAN posts a
follow-up in the thread (a correction), interrupting the auto-advance to reprioritize.

To add a Tag demo: copy `_template.py` -> `<slug>.py`, fill the constants, add
`<slug>` to `_KNOWN` below. No logic changes — pure data.
"""
from __future__ import annotations

import importlib
from types import ModuleType

# The Tag scenarios shipped in-repo. A slug must map to a module in this package.
_KNOWN = {"scheduled_exports", "blog_update"}


def load_tag_scenario(slug: str | None) -> ModuleType | None:
    """Return the Tag scenario module for ``slug``, or None for an unknown/empty slug.

    Unlike the Slack Code loader, Tag has NO default: a task that no Tag scenario
    claims is simply not a Tag task (the caller falls through to Code routing or a
    normal reply). Never raises. Import is cheap (pure data + string builders)."""
    name = (slug or "").strip()
    if name not in _KNOWN:
        return None
    try:
        return importlib.import_module(f"scenarios_tag.{name}")
    except Exception:  # noqa: BLE001 — a broken scenario must not crash routing
        return None


# Keyword -> Tag scenario routing. These are CURATED and must stay DISJOINT from the
# Slack Code keyword sets (agent.slackcode.intent_keywords + scenarios._ROUTE_KEYWORDS):
# a phrase claimed here routes to the lightweight in-thread Tag surface INSTEAD of a
# code channel, so only claim phrases the Tag scenarios genuinely own. Matched
# deterministically, most-specific first. Keep in step with each scenario's KEYWORDS.
def _route_table() -> list[tuple[str, tuple[str, ...]]]:
    table: list[tuple[str, tuple[str, ...]]] = []
    for slug in _KNOWN:
        mod = load_tag_scenario(slug)
        kws = tuple(getattr(mod, "KEYWORDS", ())) if mod else ()
        if kws:
            table.append((slug, kws))
    return table


def route_tag_scenario(task_text: str) -> str:
    """Pick the Tag scenario slug a mention claims, or "" if none.

    Returns a slug in ``_KNOWN`` when the text contains one of that scenario's
    KEYWORDS; otherwise "" (not a Tag task). Deterministic and text-only — a stale
    env default must never hijack routing. Never raises."""
    low = f" {(task_text or '').lower()} "
    for slug, kws in _route_table():
        if any(kw in low for kw in kws):
            return slug
    return ""
