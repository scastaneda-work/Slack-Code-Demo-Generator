"""Which demo would a phrase trigger? Mirrors the bot's @-mention gate in
listeners/events/app_mentioned.py, with Slack Code and Tag both switched on:
a Tag scenario's curated phrase wins first, then the coding-intent gate decides
whether a code channel opens, then route_scenario picks the story.
"""
from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Iterable
from dataclasses import dataclass

from demokit.manifest import Demo


@dataclass(frozen=True)
class Route:
    surface: str        # "claude_tag" | "slack_code" | "reply"
    scenario: str       # the slug it lands on ("" for a plain reply)
    demo: Demo | None   # the demo that owns that scenario, if any
    explanation: str    # one plain-language line


def _first(table: list[tuple[str, tuple[str, ...]]], text: str, without: set[str]) -> str:
    low = f" {(text or '').lower()} "
    return next((slug for slug, kws in table if slug not in without and any(kw in low for kw in kws)), "")


def preview(text: str, demos: list[Demo], without: Iterable[str] = ()) -> Route:
    """``without``: scenario slugs to treat as muted on top of the ones already
    muted — "what would this phrase open if this demo were off?"."""
    import scenarios
    import scenarios_tag
    from agent.slackcode import is_coding_task, load_slack_code_config

    skip = set(without)
    tag = _first(scenarios_tag._route_table(), text, skip)
    if tag:
        demo = next((d for d in demos if tag in d.tag_scenarios), None)
        return Route("claude_tag", tag, demo,
                     f"Starts an in-thread checklist for “{demo.name if demo else tag}”")

    cfg = dataclasses.replace(load_slack_code_config(), enabled=True)
    if not is_coding_task(text, cfg):
        return Route("reply", "", None,
                     "Just replies in the thread. It takes a word like build, fix or implement to open a code channel.")

    slug = _first(scenarios.route_table(), text, skip) or "freeform"
    demo = next((d for d in demos if slug in d.scenarios), None)
    if slug == "freeform":
        return Route("slack_code", slug, demo,
                     "Opens a code channel and builds it from scratch (no demo matched)")
    return Route("slack_code", slug, demo, f"Opens a code channel for “{demo.name if demo else slug}”")


def phrases(demo: Demo) -> tuple[str, ...]:
    """Every phrase that opens this demo: its scenarios' ROUTE_KEYWORDS (Slack
    Code) and KEYWORDS (Tag), in declared order, without repeats."""
    out: list[str] = []
    for pkg, slugs, attr in (("scenarios", demo.scenarios, "ROUTE_KEYWORDS"),
                             ("scenarios_tag", demo.tag_scenarios, "KEYWORDS")):
        for slug in slugs:
            try:
                mod = importlib.import_module(f"{pkg}.{slug}")
            except Exception:  # noqa: BLE001 — a broken scenario shows up in the health checks
                continue
            out += [k.strip() for k in getattr(mod, attr, ()) or () if k.strip() not in out]
    return tuple(out)


def task_words(n: int = 4) -> tuple[str, ...]:
    """A few of the words a mention needs before it opens a code channel."""
    from agent.slackcode import load_slack_code_config

    words = [k.strip() for k in load_slack_code_config().intent_keywords]
    picks = [w for w in ("build a", "fix the", "implement", "refactor", "create a") if w in words]
    return tuple((picks or words)[:n])
