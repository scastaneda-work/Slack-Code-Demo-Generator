"""Restorable delete: a demo's files move to ``.trash/`` and can be moved back.

``plan_delete`` works out exactly what would move (and what stays because
another demo also uses it) so the UI can show it before anything happens;
``delete`` carries the plan out; ``restore`` reverses it. Built-in examples
can't be deleted — hide them instead. Nothing here touches Slack.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from demokit import state
from demokit.manifest import ROOT, Demo, owned_files

TRASH_DIRNAME = ".trash"
# Folders that stay even when a delete empties them.
_KEEP_DIRS = {"scenarios", "scenarios_tag", "channels", "demos", "demos/custom", "docs", "docs/demos",
              "scenarios/assets"}


class RestoreConflict(Exception):
    """Restoring would overwrite files that exist again at their old paths."""

    def __init__(self, paths: list[str]):
        super().__init__(f"already exist: {', '.join(paths)}")
        self.paths = paths


@dataclass(frozen=True)
class DeletePlan:
    demo: Demo
    move: tuple[str, ...]                    # repo-relative paths that go to the trash
    kept_shared: tuple[tuple[str, str], ...]  # (path, name of the other demo using it)


@dataclass(frozen=True)
class TrashEntry:
    name: str           # folder name under .trash/, used to restore
    demo_id: str
    demo_name: str
    deleted_at: float
    files: tuple[str, ...]


def plan_delete(demo: Demo, demos: list[Demo], root: Path = ROOT) -> DeletePlan:
    if demo.example:
        raise ValueError(f"'{demo.name}' is a built-in example — hide it instead of deleting it")
    root = root.resolve()
    others: dict[Path, str] = {}
    for d in demos:
        if d.id != demo.id:
            for p in owned_files(d, root):
                others.setdefault(p, d.name)
    mine = owned_files(demo, root)
    rel = lambda p: str(p.relative_to(root))  # noqa: E731
    return DeletePlan(
        demo=demo,
        move=tuple(sorted(rel(p) for p in mine if p not in others)),
        kept_shared=tuple(sorted((rel(p), others[p]) for p in mine if p in others)),
    )


def _prune_empty_dirs(paths, root: Path) -> None:
    for rel in paths:
        d = (root / rel).parent
        while d != root and str(d.relative_to(root)) not in _KEEP_DIRS and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent


def delete(plan: DeletePlan, root: Path = ROOT) -> TrashEntry:
    """Move the planned files into a new .trash/<stamp>-<id>/ entry."""
    root = root.resolve()
    base = f"{time.strftime('%Y%m%d-%H%M%S')}-{plan.demo.id}"
    entry_dir, n = root / TRASH_DIRNAME / base, 1
    while entry_dir.exists():
        n += 1
        entry_dir = root / TRASH_DIRNAME / f"{base}-{n}"
    entry = TrashEntry(name=entry_dir.name, demo_id=plan.demo.id, demo_name=plan.demo.name,
                       deleted_at=time.time(), files=plan.move)
    (entry_dir / "files").mkdir(parents=True)
    # Record first, so an interrupted delete is still restorable.
    (entry_dir / "trash.json").write_text(json.dumps({
        "demo_id": entry.demo_id, "demo_name": entry.demo_name,
        "deleted_at": entry.deleted_at, "files": list(entry.files),
        "kept_shared": [list(k) for k in plan.kept_shared],
    }, indent=1) + "\n")
    for rel in plan.move:
        dest = entry_dir / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(root / rel, dest)
    _prune_empty_dirs(plan.move, root)
    state.set_hidden(plan.demo.id, (), (), hidden=False)
    return entry


def list_trash(root: Path = ROOT) -> list[TrashEntry]:
    """Newest first."""
    out = []
    for meta in (root / TRASH_DIRNAME).glob("*/trash.json"):
        try:
            m = json.loads(meta.read_text())
            out.append(TrashEntry(name=meta.parent.name, demo_id=m["demo_id"], demo_name=m["demo_name"],
                                  deleted_at=float(m["deleted_at"]), files=tuple(m["files"])))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(out, key=lambda e: e.deleted_at, reverse=True)


def restore(entry_name: str, root: Path = ROOT) -> TrashEntry:
    """Move an entry's files back. Refuses (changing nothing) if any of them
    exists again at its old path."""
    root = root.resolve()
    entry = next((e for e in list_trash(root) if e.name == entry_name), None)
    if entry is None:
        raise KeyError(f"no trash entry named {entry_name!r}")
    entry_dir = root / TRASH_DIRNAME / entry.name
    present = [rel for rel in entry.files if (entry_dir / "files" / rel).exists()]
    if conflicts := [rel for rel in present if (root / rel).exists()]:
        raise RestoreConflict(conflicts)
    for rel in present:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(entry_dir / "files" / rel, root / rel)
    shutil.rmtree(entry_dir)
    return entry


def empty_trash(entry_name: str | None = None, root: Path = ROOT) -> int:
    """Permanently remove one entry (or all). Returns how many were removed."""
    entries = [e for e in list_trash(root) if entry_name in (None, e.name)]
    for e in entries:
        shutil.rmtree(root / TRASH_DIRNAME / e.name)
    return len(entries)
