"""Template for a Claude Tag scenario. Copy to `<slug>.py`, fill the constants, and
add `<slug>` to `_KNOWN` in `scenarios_tag/__init__.py`. Pure data — no logic.

See `scenarios_tag/__init__.py` for the full contract and the turn-delta shape.
`scheduled_exports.py` (has a PR line + a replan beat) and `blog_update.py` (a
Drive/doc task, no PR) are the reference scenarios.
"""
from __future__ import annotations

SLUG = "template"  # must equal the module name and the _KNOWN entry

# Curated keywords this scenario claims. MUST stay disjoint from the Slack Code
# keyword sets (agent.slackcode.intent_keywords + scenarios._ROUTE_KEYWORDS) so a
# real build task isn't stolen into the lightweight Tag surface.
KEYWORDS: tuple[str, ...] = ()

COMMITMENT = "On it — …"  # the opening line, in Claude's voice

OPEN_IN_CLAUDE_URL = "https://claude.ai/code/session/template"  # inert visual prop


# OPTIONAL: 1–2 origin-thread humans who steer the task. email_stem resolves to
# demoeng+<stem>_<org>@slack-corp.com. Each beat's `name` must be one of these.
PARTICIPANTS: list[dict] = [
    # {"name": "Nadia", "email_stem": "nadia_nadir", "role": "backend eng"},
]


def initial_plan() -> dict:
    """The turn-0 checklist — all steps `pending`; the orchestrator flips step 0 to
    in_progress on the first post. taskplan-shaped (validated by taskplan.validate_plan;
    ≤8 steps, titles ≤200 chars)."""
    return {
        "mode": "plan",
        "title": "Task title",
        "steps": [
            # {"id": "step1", "title": "First thing", "status": "pending"},
        ],
    }


def turn_deltas() -> list[dict]:
    """Ordered per-human-follow-up mutations (see the contract in __init__.py). These
    fire only when a human posts in the thread; the auto-advance timer walks the
    checklist forward on its own otherwise. Return [] for a scenario with no scripted
    corrections."""
    return [
        # {"trigger": "any", "set": {"step1": "complete"}, "beat": {"name": "Nadia", "text": "…"}},
    ]


def result_line() -> str | None:
    """The closing line: a "✅ Up: #… " PR-style prop, or None for a non-code task
    (the orchestrator posts a plain "Done — …" close instead)."""
    return None
