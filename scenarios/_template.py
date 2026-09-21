"""Scenario template — copy to ``<slug>.py`` and fill in for a new Slack Code demo.

A scenario is the single source of truth for ONE demo story's scripted artifacts.
Everything here is hand-authored, deterministic content — the live agent only
narrates over it and answers provenance questions. Fill every field; the numbers
in the dashboard and the recap MUST match (author them once, reference twice).

Steps to add a demo:
  1. Copy this file to ``scenarios/<slug>.py``.
  2. Fill the constants + functions below.
  3. Register ``<slug>`` in ``scenarios/__init__.py`` ``_KNOWN``.
  4. Set ``SLACK_CODE_SCENARIO=<slug>`` (see ``agent.slackcode.load_slack_code_config``).
  5. Seed a matching origin thread (a ``channels/seed_<slug>.py`` script) whose
     discussion makes the trigger mention the obvious next action.
  6. Relaunch via ``ai-apps-7018/run.sh claude`` and demo.

No logic changes anywhere else — the orchestrator reads whatever scenario is active.
"""
from __future__ import annotations

# --- Identity / context-bar facts --------------------------------------------
SLUG = "template"                 # must equal the module name and the _KNOWN entry
REPO = "acme/example-service"
REPO_URL = "https://github.com/acme/example-service"
BRANCH_PREFIX = "feat/"
PR_URL = "https://github.com/acme/example-service/pull/1"

# The check the /check-<CHECK_LABEL> command runs and the context-bar pass item.
CHECK_LABEL = "quality"

# Which artifact tabs this scenario publishes — subset of {"diff","preview","dashboard"}.
# Only list the ones that make sense for the story: a visual change wants "preview"
# (a real rendered page/output), a data change wants "dashboard", every scenario
# has "diff". Don't force a "preview" onto a story with nothing to render — that
# produces a meta-diagram that undersells the feature. The orchestrator only
# publishes the artifacts named here (and always publishes the diff).
ARTIFACTS = {"diff", "preview", "dashboard"}


def base_diff() -> str:
    """Code tab, pre-patch. A unified diff string. AUTHOR IN A REAL BUT SUBTLE
    FLAW that ``check_fail_report`` will catch — that failure is the demo's peak."""
    raise NotImplementedError


def patch_diff() -> str:
    """Code tab, post-patch. The diff after the check fails — closes the flaw."""
    raise NotImplementedError


def preview_html(patched: bool = False) -> str:
    """Preview tab. ONE self-contained HTML document (inline <style> only, no JS,
    no external assets — the html view renders a single string with no network).
    The domain 'what it looks like' surface. ``patched`` flips it to the fixed
    state after /patch."""
    raise NotImplementedError


def check_fail_report() -> str:
    """Top-level FAIL verdict (Slack mrkdwn, single-asterisk bold). Deterministic —
    name the exact flaw and end by offering to patch it."""
    raise NotImplementedError


def check_pass_report() -> str:
    """Top-level PASS verdict (Slack mrkdwn). Deterministic — the clean re-check."""
    raise NotImplementedError


def dashboard_blocks() -> list[dict]:
    """Dashboard tab as a Block Kit blocks list (header/divider/section/context
    only; passed to set_view as a real array via blocks=, NOT json.dumps'd). KPI
    rows — before → after with arrow glyphs. Pull the numbers from a module-level
    constant the recap also reads."""
    raise NotImplementedError


def recap_canvas() -> str:
    """Recap tab. Canvas markdown: 5 `##` slide sections built from the SAME facts
    as the dashboard so the deck can't contradict it."""
    raise NotImplementedError


def provenance() -> dict[str, str]:
    """decision → rationale. Injected into the session's first agent turn so a
    later '@Claude why …?' answers from recorded reasoning."""
    raise NotImplementedError


def render_provenance() -> str:
    """Compact provenance block for priming the first agent turn. Usually just:
    join ``provenance()`` into '- key: why' lines."""
    raise NotImplementedError


# --- Human participants (OPTIONAL): show 1–2 origin-thread people working with
# Claude in the code channel. Omit both for a scenario with no human cast.
#   name       → spoof username shown on the chime-in (chat:write.customize).
#   email_stem → stable persona stem; the bot builds demoeng+<stem>_<orgnum>@
#                slack-corp.com and resolves it (users.lookupByEmail) to also
#                invite the real person to the roster. Portable — only the org
#                number varies, and it's derived from the bot's team url.
# Keep to ≤2 people; each beat's `name` must be one of these. Voice should match
# what the person already said in the origin seed thread.
PARTICIPANTS: list[dict] = [
    # {"name": "Ralph", "email_stem": "ralph_clark", "role": "on-call SRE"},
]


def participant_beats() -> list[dict]:
    """Scripted human chime-ins. `phase`: "arrival" (fires as the channel opens —
    the stall while Claude's first reply loads) or "on_artifacts" (fires just after
    the diff/dashboard publish — reacting to what shipped). Return [] for none."""
    return [
        # {"name": "Ralph", "phase": "arrival", "text": ":eyes: Following — …"},
        # {"name": "Elliott", "phase": "on_artifacts", "text": "Diff's up already — …"},
    ]
