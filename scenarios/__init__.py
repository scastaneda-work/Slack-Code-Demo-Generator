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

To add a demo: copy ``_template.py`` → ``<slug>.py`` and fill the module-level
constants + functions it documents, including ``ROUTE_KEYWORDS`` (the phrases
that open its code channel). The module registers itself: every non-underscore
module in this package is discovered at import, so there is no list to edit. A
private demo's stories live in ``demos/custom/<id>/scenarios/`` instead and are
discovered the same way (see ``demokit/custom.py``).
Seed a matching origin thread and relaunch. No logic changes — only data.

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
  RECAP_TITLE / RECAP_TAB: str                        # OPTIONAL — the recap canvas's title and tab
                                                      #   name (default "Leadership recap" / "Recap")
  provenance() -> dict[str, str]                      # decision → rationale (session-primed)
  render_provenance() -> str                          # provenance rendered for the first agent turn
  CHECK_LABEL: str                                    # e.g. "contrast" → /check-contrast
  ROUTE_KEYWORDS: tuple[str, ...]                     # phrases that route a mention to this story
                                                      #   (freeform declares none — it's the fallback)
  ROUTE_PRIORITY: int                                 # OPTIONAL — lower is matched first (default 50);
                                                      #   specific stories < generic ones
  PARTICIPANTS: list[dict]                            # OPTIONAL — 1–2 origin-thread humans who
                                                      #   join the channel: {name, email_stem, role}
  participant_beats() -> list[dict]                   # OPTIONAL — their scripted chime-ins:
                                                      #   {name, phase in {arrival,on_artifacts,on_asset,on_image}, text}
                                                      #   (on_asset = a designer's review when an asset link
                                                      #   is shared; on_image = a reaction after the image
                                                      #   swap; both need the image-edit hooks below)
  IMAGE_EDIT_PHRASES / image_diff(patched) /         # OPTIONAL — the pre-baked "use this image" edit;
    image_edit_report(patched)                        #   preview_html then takes image=
  SLIDE_EDIT_PHRASES / slide_diff(patched) /         # OPTIONAL — a pre-baked second edit (e.g. add a
    slide_edit_report(patched)                        #   carousel slide); preview_html then takes slide=
  preview_fallbacks(patched, *, image, slide)         # OPTIONAL — [(name, html), …] smaller-image pages
                                                      #   to try if Slack rejects a large Preview
  plan_canvas(*, image, slide, checked, patched,     # OPTIONAL — a living Plan canvas tab, ticked off
    recap) -> str                                     #   as each beat lands (only the flags it accepts)
  opening_reply() / first_pass_hint() /              # OPTIONAL — scripted first reply, first-pass next
    metrics_report()                                  #   step, and Dashboard reply (else generic lines)

Not every scenario publishes every artifact — a backend refactor has no page to preview, a
static-content change has no metrics. ARTIFACTS declares which tabs are real for the scenario;
the orchestrator only publishes those. "diff" (the Code tab) is always published.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

from demokit.custom import package_dirs
from demokit.state import hidden_slugs

_log = logging.getLogger(__name__)

# Private demos keep their stories in demos/custom/<id>/scenarios/. Adding those
# folders to this package's path makes them import as scenarios.<slug> too.
__path__ += package_dirs(__name__)

# website_redesign is the default: its HTML Preview is a real landing page (the
# hero artifact from Slack's launch demos). freeform is the SEEDLESS scenario
# (SEED = False): no scripted artifacts at all, the agent builds the Code/Preview
# artifact from the user's request and edits it live; it's also where every
# unmatched build task routes. Every code channel is live-editable regardless of
# scenario; a *scripted* scenario just seeds turn-0 content.
_DEFAULT = "website_redesign"
_DEFAULT_PRIORITY = 50


def _discover() -> tuple[set[str], dict[str, str]]:
    """Import every scenario module in this package (``_``-prefixed ones like
    ``_template`` are skipped). A module that fails to import is left out and
    recorded, so a half-written scenario can't crash bot boot — the self-test
    fails on it instead."""
    known: set[str] = set()
    errors: dict[str, str] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.ispkg:
            continue
        try:
            importlib.import_module(f"{__name__}.{info.name}")
        except Exception as e:  # noqa: BLE001 — one broken story must not take down the rest
            errors[info.name] = f"{type(e).__name__}: {e}"
            _log.error("scenario %r failed to import: %s", info.name, errors[info.name])
            continue
        known.add(info.name)
    return known, errors


# The registered scenarios (slug == module name) and any that failed to import.
_KNOWN, DISCOVERY_ERRORS = _discover()


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


# Keyword → scenario routing. Lets ONE running agent offer every story: the
# @-mention text that opens a code channel picks which one, so the SE never has to
# relaunch with a different SLACK_CODE_SCENARIO. Each scenario declares its own
# ROUTE_KEYWORDS; matched deterministically (a demo should route the same way
# every time), lowest ROUTE_PRIORITY first (ties by slug), and anything that
# didn't match a canned story falls through to `freeform` (build from scratch) —
# the intent gate (is_coding_task) already decided it's a build task. Demos the
# SE hid in ./demo are skipped (read per call, so hiding needs no restart).
def route_table() -> list[tuple[str, tuple[str, ...]]]:
    """``[(slug, keywords), …]`` in match order, hidden scenarios excluded."""
    hidden = hidden_slugs("code")
    rows = []
    for slug in _KNOWN - hidden:
        mod = importlib.import_module(f"{__name__}.{slug}")
        kws = tuple(getattr(mod, "ROUTE_KEYWORDS", ()) or ())
        if kws:
            rows.append((int(getattr(mod, "ROUTE_PRIORITY", _DEFAULT_PRIORITY)), slug, kws))
    return [(slug, kws) for _, slug, kws in sorted(rows)]


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
    for slug, kws in route_table():
        if any(kw in low for kw in kws):
            return slug
    d = (default or "").strip()
    # An explicit non-freeform default is honored only when the caller passes one
    # deliberately; the standard call passes "" so unmatched → freeform.
    if d in _KNOWN and d != "freeform":
        return d
    return "freeform"
