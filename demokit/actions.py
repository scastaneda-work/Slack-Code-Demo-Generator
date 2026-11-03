"""Run a demo's actions (its seed scripts) as subprocesses.

Every action runs with the venv's Python from the repo root, output streamed
line by line, and lands in the run log so the UI can say "last posted 12 min
ago" before a repeat. Only the option flags the manifest declares can be added —
the UI never builds a free-form command line.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from demokit import state
from demokit.manifest import ROOT, Action, Demo


def build_command(action: Action, flags: Iterable[str] = ()) -> list[str]:
    flags = list(flags)
    allowed = {o.flag for o in action.options}
    if bad := [f for f in flags if f not in allowed]:
        raise ValueError(f"not an option for '{action.label}': {' '.join(bad)}")
    return [sys.executable, *action.run, *flags]


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONUNBUFFERED": "1"}


def run_action(demo: Demo, action: Action, flags: Iterable[str] = (),
               on_line: Callable[[str], None] = print) -> int:
    """Run to completion (CLI). Returns the exit code."""
    cmd = build_command(action, flags)
    started = time.time()
    proc = subprocess.Popen(cmd, cwd=ROOT, env=_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip("\n"))
    code = proc.wait()
    state.record_run(demo.id, action.id, started, code)
    return code


async def run_action_async(demo: Demo, action: Action, flags: Iterable[str] = (),
                           on_line: Callable[[str], None] = print) -> int:
    """Same as run_action, for the TUI's event loop."""
    cmd = build_command(action, flags)
    started = time.time()
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=ROOT, env=_env(),
                                                stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    assert proc.stdout is not None
    async for raw in proc.stdout:
        on_line(raw.decode(errors="replace").rstrip("\n"))
    code = await proc.wait()
    state.record_run(demo.id, action.id, started, code)
    return code


def ago(ts: float) -> str:
    """'just now' / '12 min ago' / '3 h ago' / '2 days ago'."""
    s = max(0, time.time() - ts)
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    days = int(s // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


@dataclass(frozen=True)
class SelfTest:
    passed: int
    failed: int
    exit_code: int
    problems: tuple[str, ...]   # the [FAIL]/[WARN] lines


def self_test() -> SelfTest:
    """Run ``qa.py --self-test`` (offline, ~30s) and record the result."""
    started = time.time()
    proc = subprocess.run([sys.executable, "qa.py", "--self-test"], cwd=ROOT, env=_env(),
                          capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    result = SelfTest(
        passed=len(re.findall(r"^\[PASS\]", out, re.M)),
        failed=len(re.findall(r"^\[FAIL\]", out, re.M)),
        exit_code=proc.returncode,
        problems=tuple(line for line in out.splitlines() if line.startswith(("[FAIL]", "[WARN]"))),
    )
    state.record_self_test(started, result.passed, result.failed, result.exit_code)
    return result
