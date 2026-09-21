"""Scenario: freeform — no seed, the agent builds the artifact from scratch.

This is the *default-feeling* Code channel: a user asks for something concrete
("create a simple HTML snake game", "build me a pricing page") and Claude
generates the whole self-contained file, which lands in the Code (diff) + Preview
(html) tabs and is then editable turn-by-turn. There is NO scripted page, diff,
dashboard, check, or recap here — the artifact is whatever the agent produces,
diffed and re-rendered on every turn by the live-artifact engine.

It satisfies the scenario contract only where the live engine actually reads it:
  - SLUG / repo-ish context-bar facts (a neutral sandbox, branch base "master")
  - ARTIFACTS = {"diff","preview"}  (the two live tabs; no scripted dashboard)
  - SEED = False + seed_filename()  → the engine seeds nothing and derives the
    filename from the first request instead.

The scripted-only members (base_diff/patch_diff/check_*/dashboard/recap/
provenance) are intentionally ABSENT: this scenario's `SEED = False` tells the
orchestrator not to run the scripted check→patch→recap beats, and qa's per-
scenario contract loop skips the scripted-content assertions for a seedless
scenario (it validates the seed contract instead). Do not add scripted content
here — that's what website_redesign / billing_webhook are for.
"""
from __future__ import annotations

# --- Identity / context-bar facts --------------------------------------------
SLUG = "freeform"
# Neutral placeholder repo/branch — the artifact isn't a real repo file, but the
# context bar reads naturally. Branch base is "master" to match the live examples
# (the Code tab in the screenshots shows `Branch master`).
REPO = "sandbox/scratch"
REPO_URL = "https://example.com/sandbox/scratch"
BRANCH_PREFIX = ""            # no feat/ prefix — freeform builds live on master
PR_URL = ""
CHECK_LABEL = "build"         # unused (no scripted check), present for the contract

# The live tabs this scenario drives. No "dashboard" — a freeform build has no
# scripted metrics. "diff" (Code) + "preview" (the rendered result) only.
ARTIFACTS = {"diff", "preview"}

# This scenario seeds NO turn-0 artifact — the agent builds it from the request.
# The orchestrator checks SEED to decide whether to seed and whether the scripted
# check/patch/recap actions apply (they don't, for a freeform channel).
SEED = False


def seed_filename() -> str:
    """Freeform channels have no seed; the engine derives the filename from the
    first request (e.g. "create a snake game" → snake-game.html). Returns "" so a
    caller that asks unconditionally gets a falsy value and falls back to deriving."""
    return ""
