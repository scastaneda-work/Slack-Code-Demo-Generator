"""Slack Code demo *scenarios* — the single source of truth for a demo story.

A **scenario** is one Python module holding every scripted artifact a Slack Code
session shows: the code diff, the "what it looks like" preview (HTML), the
check→patch→re-check verdicts, the reliability dashboard, the leadership recap
canvas, and the decision-provenance facts. Every artifact in a session imports
from ONE scenario module so the numbers can never disagree across tabs.

Why a module and not config: the artifacts are large, structured, hand-authored
content (a full HTML page, unified diffs, Block Kit rows). Env vars only pick
*which* scenario is active (``SLACK_CODE_SCENARIO``); the content lives in code
so it's diffable, self-testable offline, and identical every demo run.

To add a demo: copy ``_template.py`` → ``<slug>.py``, fill the module-level
constants + functions it documents, set ``SLACK_CODE_SCENARIO=<slug>`` (see
``agent.slackcode.load_slack_code_config``), seed a matching origin thread, and
relaunch. No logic changes — only data.

Contract every scenario module must satisfy (duck-typed; see ``_template.py``):
  SLUG: str
  REPO / REPO_URL / BRANCH_PREFIX / PR_URL: str      # context-bar + branch facts
  ARTIFACTS: set[str]                                 # which tabs this scenario publishes,
                                                      #   subset of {"diff","preview","dashboard"}
                                                      #   ("diff" is always published regardless)
  base_diff() -> str                                  # the Code tab (pre-patch)
  patch_diff() -> str                                 # the Code tab (post-patch)
  preview_html(patched: bool) -> str                  # the Preview tab (self-contained HTML) —
                                                      #   only needed if "preview" in ARTIFACTS
  check_fail_report() -> str                          # top-level FAIL verdict (mrkdwn)
  check_pass_report() -> str                          # top-level PASS verdict (mrkdwn)
  dashboard_blocks() -> list[dict]                    # the Dashboard tab (Block Kit) —
                                                      #   only needed if "dashboard" in ARTIFACTS
  recap_canvas() -> str                               # the recap (posted as a message)
  provenance() -> dict[str, str]                      # decision → rationale (session-primed)
  render_provenance() -> str                          # provenance rendered for the first agent turn
  CHECK_LABEL: str                                    # e.g. "contrast" → /check-contrast
  PARTICIPANTS: list[dict]                            # OPTIONAL — 1–2 origin-thread humans who
                                                      #   join the channel: {name, email_stem, role}
  participant_beats() -> list[dict]                   # OPTIONAL — their scripted chime-ins:
                                                      #   {name, phase in {arrival,on_artifacts}, text}

Not every scenario publishes every artifact — a backend refactor has no page to preview, a
static-content change has no metrics. ARTIFACTS declares which tabs are real for the scenario;
the orchestrator only publishes those. "diff" (the Code tab) is always published.
"""
from __future__ import annotations

import importlib
from types import ModuleType

# The scenarios shipped in-repo. A slug must map to a module in this package.
# website_redesign is the default: its HTML Preview is a real landing page (the
# hero artifact from Slack's launch demos). billing_webhook is the backend story
# (diff + dashboard, no preview) — opt in via SLACK_CODE_SCENARIO=billing_webhook.
# freeform is the SEEDLESS scenario (SEED = False): no scripted artifacts at all,
# the agent builds the Code/Preview artifact from the user's request and edits it
# live — opt in via SLACK_CODE_SCENARIO=freeform. Every code channel is live-
# editable regardless of scenario; a *scripted* scenario just seeds turn-0 content.
_KNOWN = {
    "website_redesign", "billing_webhook", "freeform",
    "checkout_incident", "flaky_test", "sql_migration",
}
_DEFAULT = "website_redesign"


def is_seeded(scenario) -> bool:
    """True if the scenario seeds a scripted turn-0 artifact (and supports the
    scripted check→patch→recap beats). A scenario opts OUT by declaring
    ``SEED = False`` (freeform); scripted scenarios omit it and default to seeded.
    Used by the orchestrator to decide whether to seed the first turn and whether
    the scripted-action shortcuts apply."""
    return getattr(scenario, "SEED", True) is not False


def load_scenario(slug: str | None) -> ModuleType:
    """Return the scenario module for ``slug`` (defaults to website_redesign).

    Never raises on an unknown/typo'd slug — falls back to the default so a bad
    ``SLACK_CODE_SCENARIO`` value degrades to a working demo rather than crashing
    a live session. Import is cheap (pure data + string builders, no I/O)."""
    name = (slug or _DEFAULT).strip() or _DEFAULT
    if name not in _KNOWN:
        name = _DEFAULT
    return importlib.import_module(f"scenarios.{name}")


# Keyword → scenario routing. Lets ONE running agent offer all three stories: the
# @-mention text that opens a code channel picks which one, so Sergio never has to
# relaunch with a different SLACK_CODE_SCENARIO. Matched deterministically (a demo
# should route the same way every time), most-specific stories first, and anything
# that didn't match a canned story falls through to `freeform` (build from
# scratch) — the intent gate (is_coding_task) already decided it's a build task.
_ROUTE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # billing first: "refactor the billing webhook" also contains "refactor", but
    # it's the billing story, so match its specific phrases before anything else.
    ("billing_webhook", (
        "billing webhook", "stripe webhook", "webhook refactor", "durable queue",
        "billing cron", "webhook to a queue", "webhook durability",
    )),
    # checkout incident: the P1 latency/rollback story. Kept specific so it doesn't
    # swallow a generic "fix" — "checkout"/"payment-service"/"retry storm"/"rollback".
    ("checkout_incident", (
        "checkout api", "checkout latency", "payment-service", "payment service",
        "retry storm", "roll back the", "rollback the", "p99", "latency spike",
        "orders failing", "incident",
    )),
    # flaky test: the everyday CI-flake story.
    ("flaky_test", (
        "flaky", "flake", "flakey", "login_redirect", "login redirect test",
        "re-run", "rerun the test", "test is failing intermittently",
    )),
    # sql migration: the schema-change story.
    ("sql_migration", (
        "migration", "add a column", "add a last_login", "last_login_at",
        "schema change", "alter table", "backfill", "add a field to",
    )),
    ("website_redesign", (
        "homepage", "home page", "landing page", "redesign", "hero section",
        "marketing site", "welloguard", "the site", "web page redesign",
    )),
]


def route_scenario(task_text: str, default: str = "") -> str:
    """Pick the scenario slug for a channel-opening mention from its text.

    Returns a slug in ``_KNOWN``. Canned stories match their keywords; ANY
    unmatched build/coding task → ``freeform`` (build from scratch). Routing is by
    text only — it does NOT fall back to the env ``SLACK_CODE_SCENARIO``, because
    the whole point is that one running agent serves all three by keyword, so a
    stale env default must not hijack a freeform ("snake game") request into the
    seeded website story. ``default`` is accepted only as an explicit override for
    a caller that WANTS to force a story when the text is a canned keyword-less
    phrasing — pass "" (the norm) to always route by text. Never raises."""
    low = f" {(task_text or '').lower()} "
    for slug, kws in _ROUTE_KEYWORDS:
        if any(kw in low for kw in kws):
            return slug
    d = (default or "").strip()
    # An explicit non-freeform default is honored only when the caller passes one
    # deliberately; the standard call passes "" so unmatched → freeform.
    if d in _KNOWN and d != "freeform":
        return d
    return "freeform"
