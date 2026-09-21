"""Tag scenario: update the beta blog post (a Drive/doc task — NO PR).

Mirrors the second screenshot: @Claude is asked to add a line to the launch blog
under "What's in the beta". Claude grabs the doc from Drive, drafts the line, and
updates it in place — a connected-tool task, not a code build. The checklist ticks
itself forward; there's no PR, so it closes with a plain "Done — …" line
(result_line() -> None).

Demonstrates that Tag isn't code-only: the same in-thread live-checklist surface
covers lightweight doc/ops work using the org's connected tools. All FAKE props.
"""
from __future__ import annotations

SLUG = "blog_update"

# Doc/Drive phrasing — these never trip the Slack Code build gate, so they land in
# Tag naturally; claiming them here just makes the routing explicit + demo-repeatable.
KEYWORDS = (
    "update the blog", "beta blog", "launch blog", "blog post",
    "what's in the beta", "whats in the beta", "add a line under", "feature table",
)

COMMITMENT = (
    "On it — updating the beta blog post. Checklist below; I'll tick it off as I go."
)

OPEN_IN_CLAUDE_URL = "https://claude.ai/code/session/blog-update"

# A lightweight doc task — no scripted human cast by default (keep it simple).
PARTICIPANTS: list[dict] = []


def initial_plan() -> dict:
    return {
        "mode": "plan",
        "title": "Update the beta blog",
        "steps": [
            {"id": "grab", "title": "Grab the latest blog from Drive", "status": "pending"},
            {"id": "find", "title": "Find the “What's in the beta” section", "status": "pending"},
            {"id": "draft", "title": "Draft the new line", "status": "pending"},
            {"id": "save", "title": "Insert & save the doc", "status": "pending"},
        ],
    }


def turn_deltas() -> list[dict]:
    """Optional human follow-ups. The demo's canonical one is a wording tweak, which
    inserts a quick "Revising the wording" step. Fires only on a human follow-up."""
    return [
        {
            "trigger": "any",
            "set": {"draft": "complete"},
            "insert": {
                "after": "draft",
                "id": "revise",
                "title": "Revising the wording",
                "status": "in_progress",
            },
        },
    ]


def result_line() -> str | None:
    """No PR for a doc task — the orchestrator posts a plain "Done — …" close instead."""
    return None
