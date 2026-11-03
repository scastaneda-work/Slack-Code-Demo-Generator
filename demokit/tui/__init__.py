"""The terminal UI (``./demo`` with no arguments)."""


def launch() -> int:
    try:
        import textual  # noqa: F401
    except ImportError:
        print("[demo] The terminal UI needs one more package. In your Terminal, run:\n"
              "  .venv/bin/pip install -r requirements.txt\n"
              "then run ./demo again. (./demo --help lists the commands that work without it.)")
        return 1
    from demokit.tui.app import run
    return run()
