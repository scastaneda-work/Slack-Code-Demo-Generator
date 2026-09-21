"""Tag scenario: scheduled exports (the hero in-thread demo, mirrors the screenshots).

A teammate tags @Claude in a channel thread to build scheduled exports. Claude
commits, drops a live TODO checklist that ticks itself forward, and MID-BUILD a real
teammate corrects the design ("workspace-level, admins set it for everyone — not
per-user"), so Claude visibly REPLANS (a "Replanning — …" step) and adjusts the
remaining todos. It closes with a scripted PR-style result line.

This is the lightweight counterpart to a Slack Code channel: everything happens in
the thread, the checklist is one message updated in place, no channel is spun up.
The human corrections come from REAL teammates (their own user tokens) — Claude does
NOT spoof personas in a thread (it only does that inside a dedicated code channel).
All artifacts are FAKE demo props.
"""
from __future__ import annotations

SLUG = "scheduled_exports"

# What route_tag_scenario() claims. MUST stay disjoint from the Slack Code keyword
# sets — "scheduled export" is build-ish, so claiming it here is what makes it route
# to the lightweight Tag surface instead of a full code channel.
KEYWORDS = (
    "scheduled export", "scheduled exports", "schedule exports", "schedule the export",
    "export the audit log", "recurring export", "nightly export", "audit log export",
)

COMMITMENT = (
    "On it — I'll build scheduled exports. Dropping a checklist here and ticking it "
    "off as I go; shout if I've got the shape wrong."
)

# The inert "Open session in Claude" prop (Slack has no working deep-link that runs a
# session, so this is a plausible-looking but non-functional URL).
OPEN_IN_CLAUDE_URL = "https://claude.ai/code/session/scheduled-exports"

# No scripted (spoofed) cast — the corrections come from REAL teammates posting with
# their own user tokens (Claude never spoofs personas in a thread). Kept empty for the
# contract; the demo driver picks which real token-holder voices the correction.
PARTICIPANTS: list[dict] = []


def initial_plan() -> dict:
    """The turn-0 checklist (all steps pending; the orchestrator flips step 0 to
    in_progress when it first posts). taskplan-shaped so taskplan.validate_plan passes."""
    return {
        "mode": "plan",
        "title": "Scheduled exports",
        "steps": [
            {"id": "scope", "title": "Confirm export scope & destination", "status": "pending"},
            {"id": "job", "title": "Add the export job", "status": "pending"},
            {"id": "schedule", "title": "Wire the nightly schedule", "status": "pending"},
            {"id": "tests", "title": "Add the integration tests", "status": "pending"},
            {"id": "pr", "title": "Open the PR", "status": "pending"},
        ],
    }


def turn_deltas() -> list[dict]:
    """Per-follow-up mutations. These fire ONLY when a human posts in the thread
    (interrupting the auto-advance). The canonical demo follow-up is Nadia's
    workspace-level correction — it inserts a Replanning step and re-scopes the job.

    Turn indices are 0-based over human follow-ups. The auto-advance timer walks the
    checklist forward on its own between (and after) these."""
    return [
        {
            # First human correction: workspace-level, not per-user (matches the video).
            # Fires when a real teammate posts the correction in the thread.
            "trigger": "any",
            "set": {"scope": "complete", "job": "in_progress"},
            "insert": {
                "after": "scope",
                "id": "replan",
                "title": "Replanning — workspace-level (admin-set), new workspace_schedules table",
                "status": "in_progress",
            },
        },
        {
            # Second correction (optional in the demo): they want to pick the cadence.
            "trigger": "cadence",
            "set": {"replan": "complete"},
            "insert": {
                "after": "schedule",
                "id": "cadence",
                "title": "Add a cadence picker (e.g. every Monday), not just on/off",
                "status": "in_progress",
            },
        },
    ]


def result_line() -> str | None:
    """The closing PR-style line (a FAKE demo prop, matching the screenshot's shape)."""
    return (
        ":white_check_mark: Up: <"
        "https://github.com/acme/platform/pull/4131|#4131> "
        "feat: scheduled exports (workspace-level, cadence picker) · `+612 −38` · suite green"
    )
