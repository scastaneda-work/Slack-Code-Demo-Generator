"""Local demo-manager state: hidden demos and the action run log.

One gitignored JSON file at the repo root. The bot only READS it (the scenario
routers skip hidden slugs); demokit writes it. A missing or unreadable file
means nothing is hidden and nothing has run. Stdlib only — the routers import
this at boot.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = Path(os.environ.get("DEMO_MANAGER_STATE_PATH") or ROOT / ".demo_manager_state.json")

# Which state key holds the hidden slugs for each router.
_HIDDEN_KEYS = {"code": "hidden_scenarios", "tag": "hidden_tag_scenarios"}


def load() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def hidden_slugs(kind: str) -> set[str]:
    """Scenario slugs the ``kind`` router ("code" or "tag") should skip."""
    return {s for s in load().get(_HIDDEN_KEYS[kind], []) if isinstance(s, str)}


def save(data: dict) -> None:
    """Write atomically (tmp + rename) so a crash never leaves half a file."""
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    os.replace(tmp, STATE_PATH)


def hidden_demo_ids() -> set[str]:
    return set(load().get("hidden_demos", {}))


def set_hidden(demo_id: str, scenarios: tuple[str, ...], tag_scenarios: tuple[str, ...],
               hidden: bool) -> None:
    """Hide/unhide a demo. The per-demo entry remembers which slugs it took out of
    routing; the flat lists the routers read are rebuilt from those entries."""
    data = load()
    demos = data.get("hidden_demos", {})
    if hidden:
        demos[demo_id] = {"scenarios": list(scenarios), "tag_scenarios": list(tag_scenarios)}
    else:
        demos.pop(demo_id, None)
    data["hidden_demos"] = demos
    data[_HIDDEN_KEYS["code"]] = sorted({s for e in demos.values() for s in e.get("scenarios", [])})
    data[_HIDDEN_KEYS["tag"]] = sorted({s for e in demos.values() for s in e.get("tag_scenarios", [])})
    save(data)


_MAX_RUNS = 200


def record_run(demo_id: str, action_id: str, started: float, exit_code: int) -> None:
    data = load()
    runs = data.get("runs", [])
    runs.append({"demo": demo_id, "action": action_id, "started": started, "exit_code": exit_code})
    data["runs"] = runs[-_MAX_RUNS:]
    save(data)


def last_run(demo_id: str, action_id: str) -> dict | None:
    return next((r for r in reversed(load().get("runs", []))
                 if r.get("demo") == demo_id and r.get("action") == action_id), None)


def record_self_test(at: float, passed: int, failed: int, exit_code: int) -> None:
    data = load()
    data["self_test"] = {"at": at, "passed": passed, "failed": failed, "exit_code": exit_code}
    save(data)


def last_self_test() -> dict | None:
    return load().get("self_test")


_FEATURE_DEFAULTS = {"slack_code": True, "tag": True}


def bot_features() -> dict[str, bool]:
    """Which bot features ./demo turns on when it starts the bot."""
    saved = load().get("bot_features") or {}
    return {k: bool(saved.get(k, v)) for k, v in _FEATURE_DEFAULTS.items()}


def set_bot_features(slack_code: bool, tag: bool) -> None:
    data = load()
    data["bot_features"] = {"slack_code": slack_code, "tag": tag}
    save(data)
