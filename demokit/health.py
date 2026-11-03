"""Health checks: is each demo intact, does its trigger phrase land on it, and is
this machine set up? Pure reads — nothing here changes files or calls Slack.
Token VALUES are never returned, only whether they look filled in.
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from demokit import routing, state
from demokit.manifest import ROOT, Demo


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str = ""
    warn: bool = False   # when not ok: a warning, not an error


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def demo_checks(demo: Demo, demos: list[Demo], root: Path = ROOT) -> list[Check]:
    import scenarios
    import scenarios_tag

    checks = [Check(f"manifest: {p}", False) for p in demo.problems]
    # A private demo's stories sit in its own folder: demos/custom/<id>/scenarios/.
    base = f"{_rel(demo.folder, root)}/" if demo.folder else ""
    for pkg, slugs, folder in ((scenarios, demo.scenarios, f"{base}scenarios"),
                               (scenarios_tag, demo.tag_scenarios, f"{base}scenarios_tag")):
        for slug in slugs:
            if slug in pkg.DISCOVERY_ERRORS:
                checks.append(Check(f"{folder}/{slug}.py loads", False, pkg.DISCOVERY_ERRORS[slug]))
            else:
                checks.append(Check(f"{folder}/{slug}.py exists", slug in pkg._KNOWN))
    for a in demo.actions:
        if a.run:
            checks.append(Check(f"{a.run[0]} exists", (root / a.run[0]).is_file()))
    for d in demo.docs:
        checks.append(Check(f"{d.path} exists", (root / d.path).exists()))
    for pattern in demo.files:
        p = pattern.rstrip("/")
        checks.append(Check(f"{pattern} exists", (root / p).exists() or any(root.glob(p))))

    wants = [("slack_code", demo.scenarios), ("claude_tag", demo.tag_scenarios)]
    wants = [(surface, slugs) for surface, slugs in wants if slugs]
    if wants and not demo.trigger:
        checks.append(Check("has a trigger phrase", False))
    elif wants and demo.id not in state.hidden_demo_ids():
        r = routing.preview(demo.trigger, demos)
        ok = any(r.surface == surface and r.scenario in slugs for surface, slugs in wants)
        checks.append(Check("trigger phrase opens this demo", ok, "" if ok else r.explanation))
    seen: set[str] = set()  # a seed listed as an action AND in files is checked once
    return [c for c in checks if not (c.label in seen or seen.add(c.label))]


def repo_checks(demos: list[Demo], root: Path = ROOT) -> list[Check]:
    """Scenarios that failed to import, or that no manifest owns; and two
    manifests with the same id (a shared demo and a private copy of it)."""
    import scenarios
    import scenarios_tag

    checks = []
    by_id: dict[str, list[Demo]] = {}
    for d in demos:
        by_id.setdefault(d.id, []).append(d)
    for demo_id, same in sorted(by_id.items()):
        if len(same) > 1:
            where = " and ".join(_rel(d.path, root) for d in same)
            checks.append(Check(f"{demo_id} has one manifest", False, f"{where} — delete or rename one"))
    for pkg, attr in ((scenarios, "scenarios"), (scenarios_tag, "tag_scenarios")):
        owners: dict[str, list[str]] = {}
        for d in demos:
            for slug in getattr(d, attr):
                owners.setdefault(slug, []).append(d.id)
        for slug, err in sorted(pkg.DISCOVERY_ERRORS.items()):
            checks.append(Check(f"{pkg.__name__}/{slug}.py loads", False, err))
        for slug in sorted(pkg._KNOWN - owners.keys()):
            checks.append(Check(f"{pkg.__name__}/{slug}.py belongs to a demo", False,
                                "no demo manifest lists it, so ./demo can't show it", warn=True))
        for slug, ids in sorted(owners.items()):
            if len(ids) > 1:
                checks.append(Check(f"{pkg.__name__}/{slug}.py has one owner", False,
                                    f"listed by {', '.join(ids)}", warn=True))
    return checks


def _placeholder(v) -> bool:
    return not isinstance(v, str) or not v.strip() or "PASTE" in v


def setup_checks(root: Path = ROOT) -> list[Check]:
    """First-run checklist for this machine (tokens, venv, CLIs)."""
    checks = []
    tok = root / "tokens.json"
    if not tok.exists():
        checks.append(Check("tokens.json exists", False, "cp tokens.example.json tokens.json  (see SETUP.md Step 3)"))
    else:
        mode = tok.stat().st_mode & 0o777
        checks.append(Check("tokens.json is private (chmod 600)", not mode & 0o077, f"chmod 600 tokens.json  (now {oct(mode)})"))
        try:
            t = json.loads(tok.read_text())
        except (OSError, ValueError):
            t = None
        if not isinstance(t, dict):
            checks.append(Check("tokens.json is valid JSON", False, "open it in your editor and fix the syntax"))
        else:
            bot = next(iter((t.get("bots") or {}).values()), {})
            checks += [
                Check("workspace_id is set", not _placeholder(t.get("workspace_id")), "set workspace_id in tokens.json"),
                Check("bot token is filled in", not _placeholder(bot.get("bot_token")),
                      "paste your xoxb- token into tokens.json in your editor (never into chat)"),
                Check("app token is filled in", not _placeholder(bot.get("app_token")),
                      "paste your xapp- token into tokens.json in your editor (never into chat)"),
            ]
    checks.append(Check("Python 3.10+ in the venv", sys.version_info >= (3, 10),
                        "rebuild: python3.12 -m venv .venv  (SETUP.md Step 5)"))
    checks.append(Check("Slack CLI installed", bool(shutil.which("slack")), "install the Slack CLI, then: slack login"))
    checks.append(Check("Slack CLI logged in", (Path.home() / ".slack" / "credentials.json").exists(), "slack login"))
    checks.append(Check("claude CLI installed", bool(shutil.which("claude")),
                        "install Claude Code (or set ANTHROPIC_API_KEY in .env)", warn=True))
    return checks
