"""demokit — the demo manager behind ``./demo`` (TUI + CLI).

A *demo* is a manifest in ``demos/<id>.toml`` (or a private demo's
``demos/custom/<id>/demo.toml``) tying together the scenarios, seed scripts,
docs and assets one customer story uses. demokit lists them,
runs their actions (seed scripts), hides them from routing, and moves them to a
restorable ``.trash/``.

Keep this ``__init__`` import-free: the bot's scenario routers import
``demokit.state`` at boot and must not pull in the rest of the manager.
"""
