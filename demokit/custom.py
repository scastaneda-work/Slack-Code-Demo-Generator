"""Private demos: one folder per demo under ``demos/custom/``, git-ignored.

A private demo keeps every file it owns in its own folder, laid out like the
repo, so sharing it later is just moving the files up:

    demos/custom/<id>/
    ├── demo.toml                 its manifest
    ├── scenarios/<slug>.py       (+ scenarios/assets/)
    ├── scenarios_tag/<slug>.py
    ├── channels/seed_<slug>.py
    └── docs/

``demos/custom/`` is in .gitignore, so a new demo stays on this machine unless
it's moved out, and no shared file has to name the customer. Stdlib only: the
scenario packages import this at boot.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CUSTOM_DIR = Path("demos") / "custom"   # repo-relative
MANIFEST_NAME = "demo.toml"


def package_dirs(package: str, root: Path = ROOT) -> list[str]:
    """Every private demo's ``<package>/`` folder (e.g. ``scenarios``). A scenario
    package adds these to its ``__path__`` so their modules import as
    ``<package>.<slug>``, exactly like the shared ones."""
    return [str(p) for p in sorted((root / CUSTOM_DIR).glob(f"*/{package}")) if p.is_dir()]
