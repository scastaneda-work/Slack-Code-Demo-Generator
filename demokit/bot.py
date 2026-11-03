"""Run the bot (``./run.sh``) as a child of the demo manager.

run.sh ends in ``exec slack run …`` and the Slack CLI expects a terminal, so the
child gets a pseudo-terminal and its own process group. A background thread
drains its output into a buffer the UI reads on a timer — it keeps draining even
while the UI hands the terminal to Claude Code (``New demo``), so the bot never
stalls on a full pipe mid-demo.

Stopping sends Ctrl-C (SIGINT) to the whole group (the Slack CLI, the Python app
it starts, and run.sh's background manifest-reconcile), then escalates to
SIGTERM/SIGKILL if anything is still alive. If the manager exits or its terminal
closes, the group is stopped too, so no bot is left running unseen.
"""
from __future__ import annotations

import atexit
import fcntl
import os
import pty
import re
import signal
import struct
import subprocess
import termios
import threading
import time
import weakref
from collections import deque

from demokit.manifest import ROOT

# CSI sequences other than colours (cursor moves, line clears) — dropped; colour
# codes (…m) are kept for the log to render.
_NON_SGR_CSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-ln-~]|\x1b\][^\x07]*\x07")

_LIVE: weakref.WeakSet[BotProcess] = weakref.WeakSet()


def _clean(raw: bytes) -> str:
    raw = _NON_SGR_CSI.sub(b"", raw).rstrip(b"\r")
    # A spinner redraws with \r; keep only the last state of the line.
    return raw.rsplit(b"\r", 1)[-1].decode(errors="replace")


def _stop_all(*_args) -> None:
    for bot in list(_LIVE):
        bot.kill_now()


_handlers_installed = False


def _install_exit_handlers() -> None:
    global _handlers_installed
    if _handlers_installed:
        return
    _handlers_installed = True
    atexit.register(_stop_all)

    def on_hangup(signum, frame):  # the Terminal window was closed
        _stop_all()
        os._exit(128 + signum)

    signal.signal(signal.SIGHUP, on_hangup)


class BotProcess:
    def __init__(self, command: tuple[str, ...] = ("./run.sh",), cwd=ROOT, max_lines: int = 5000):
        self.command = list(command)
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self._pgid: int | None = None
        self._lines: deque[str] = deque(maxlen=max_lines)

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def exit_code(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    def start(self, env: dict[str, str]) -> None:
        if self.running:
            raise RuntimeError("the bot is already running")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        try:
            self.proc = subprocess.Popen(
                self.command, cwd=self.cwd, env={**os.environ, **env},
                stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, close_fds=True,
            )
        finally:
            os.close(slave)
        self._pgid = self.proc.pid  # a session leader's pgid is its pid
        _LIVE.add(self)
        _install_exit_handlers()
        threading.Thread(target=self._pump, args=(master,), daemon=True).start()

    def _pump(self, master: int) -> None:
        buf = b""
        while True:
            try:
                data = os.read(master, 65536)
            except OSError:  # EIO once the child side closes
                data = b""
            if not data:
                break
            buf += data
            *done, buf = buf.split(b"\n")
            self._lines.extend(_clean(line) for line in done)
        if buf:
            self._lines.append(_clean(buf))
        os.close(master)

    def drain(self) -> list[str]:
        """Output lines since the last drain."""
        out = []
        while self._lines:
            try:
                out.append(self._lines.popleft())
            except IndexError:
                break
        return out

    def _group_alive(self) -> bool:
        if self.proc is not None:
            self.proc.poll()  # reap the leader so it doesn't linger as a zombie
        if self._pgid is None:
            return False
        try:
            os.killpg(self._pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def stop(self, grace: float = 8.0) -> None:
        """Ctrl-C the whole group, escalating if it doesn't exit. Blocks (call it
        off the UI thread)."""
        for sig, wait in ((signal.SIGINT, grace), (signal.SIGTERM, 3.0), (signal.SIGKILL, 2.0)):
            if not self._group_alive():
                break
            try:
                os.killpg(self._pgid, sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline and self._group_alive():
                time.sleep(0.1)
        _LIVE.discard(self)

    def kill_now(self) -> None:
        """Last resort on exit: SIGTERM the group without waiting."""
        if self._pgid is not None and self._group_alive():
            try:
                os.killpg(self._pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def other_bot_pids(mine: BotProcess | None = None) -> list[int]:
    """Bots started outside the manager (e.g. ./run.sh in another Terminal).
    Two copies split Slack's events between them, so the UI warns first."""
    try:
        out = subprocess.run(["pgrep", "-f", "slack run -w"], capture_output=True, text=True).stdout
    except OSError:
        return []
    pids = [int(p) for p in out.split()]
    own = mine._pgid if mine and mine.running else None
    result = []
    for pid in pids:
        try:
            if os.getpgid(pid) != own:
                result.append(pid)
        except ProcessLookupError:
            continue
    return result
