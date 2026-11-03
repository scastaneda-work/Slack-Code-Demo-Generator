"""Demo manifests — ``demos/<id>.toml``, one per demo.

A manifest ties together everything one demo uses: the scenarios it owns, its
seed-script actions, docs, assets and Slack channels. It's the only place that
knows "these files are one demo", so listing, running, hiding and deleting all
start here. See ``demos/README.md`` for the field reference.

A private demo's manifest is ``demos/custom/<id>/demo.toml`` instead, and the
demo owns its whole folder (see ``demokit/custom.py``). Paths inside it are
still repo-relative, like every other manifest's.

Loading never raises on a bad manifest: problems are collected on
``Demo.problems`` so the UI can show them next to the demo instead of crashing.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from demokit.custom import CUSTOM_DIR, MANIFEST_NAME

ROOT = Path(__file__).resolve().parent.parent
SURFACES = {"slack_code", "claude_tag", "slack"}


@dataclass(frozen=True)
class Option:
    flag: str
    label: str


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    run: tuple[str, ...]           # script path (repo-relative) + fixed args
    options: tuple[Option, ...] = ()
    destructive: bool = False      # forces a confirm (CLI: --yes)
    confirm: str = ""              # custom confirm text
    repeat_warning: str = ""       # shown when this action has run before
    note: str = ""                 # one line under the button: what it does
    step: str = ""                 # the step title in ./demo (first action), e.g. "Set the scene"
    preview: bool = False          # changes nothing in Slack; ./demo shows its output in a window


@dataclass(frozen=True)
class Doc:
    label: str
    path: str


@dataclass(frozen=True)
class Demo:
    id: str
    path: Path
    name: str
    customer: str = ""
    summary: str = ""
    surfaces: tuple[str, ...] = ()
    scenarios: tuple[str, ...] = ()
    tag_scenarios: tuple[str, ...] = ()
    trigger: str = ""
    channels: tuple[str, ...] = ()
    example: bool = False
    files: tuple[str, ...] = ()
    docs: tuple[Doc, ...] = ()
    actions: tuple[Action, ...] = ()
    problems: tuple[str, ...] = field(default=(), compare=False)

    def action(self, action_id: str) -> Action | None:
        return next((a for a in self.actions if a.id == action_id), None)

    @property
    def folder(self) -> Path | None:
        """A private demo's own folder (``demos/custom/<id>/``); None for a shared one."""
        return self.path.parent if self.path.name == MANIFEST_NAME else None


def _strs(raw, key: str, problems: list[str]) -> tuple[str, ...]:
    val = raw.get(key, [])
    if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
        problems.append(f"`{key}` must be a list of strings")
        return ()
    return tuple(val)


def _str(raw, key: str, problems: list[str], required: bool = False) -> str:
    val = raw.get(key, "")
    if not isinstance(val, str):
        problems.append(f"`{key}` must be a string")
        return ""
    if required and not val.strip():
        problems.append(f"`{key}` is required")
    return val


def _parse_action(raw, problems: list[str]) -> Action | None:
    if not isinstance(raw, dict):
        problems.append("each [[actions]] entry must be a table")
        return None
    aid = _str(raw, "id", problems, required=True)
    where = f"action `{aid or '?'}`"
    run = raw.get("run", [])
    if not (isinstance(run, list) and run and all(isinstance(r, str) for r in run)):
        problems.append(f"{where}: `run` must be a non-empty list of strings")
        run = []
    options = []
    for opt in raw.get("options", []):
        flag = opt.get("flag", "") if isinstance(opt, dict) else ""
        if not (isinstance(flag, str) and flag.startswith("--")):
            problems.append(f"{where}: each option needs a `flag` starting with --")
            continue
        options.append(Option(flag=flag, label=str(opt.get("label") or flag)))
    return Action(
        id=aid,
        label=_str(raw, "label", problems, required=True),
        run=tuple(run),
        options=tuple(options),
        destructive=bool(raw.get("destructive", False)),
        confirm=_str(raw, "confirm", problems),
        repeat_warning=_str(raw, "repeat_warning", problems),
        note=_str(raw, "note", problems),
        step=_str(raw, "step", problems),
        preview=bool(raw.get("preview", False)),
    )


def parse(path: Path) -> Demo:
    """One manifest → a Demo (problems collected, never raised)."""
    demo_id = path.parent.name if path.name == MANIFEST_NAME else path.stem
    problems: list[str] = []
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        return Demo(id=demo_id, path=path, name=demo_id, problems=(f"can't read manifest: {e}",))

    surfaces = _strs(raw, "surfaces", problems)
    if unknown := set(surfaces) - SURFACES:
        problems.append(f"unknown surface(s): {', '.join(sorted(unknown))}")

    docs = []
    for d in raw.get("docs", []):
        if isinstance(d, dict) and isinstance(d.get("path"), str):
            docs.append(Doc(label=str(d.get("label") or Path(d["path"]).name), path=d["path"]))
        else:
            problems.append("each docs entry needs a `path`")

    actions = [a for a in (_parse_action(r, problems) for r in raw.get("actions", [])) if a]
    ids = [a.id for a in actions]
    if len(ids) != len(set(ids)):
        problems.append("action ids must be unique")

    return Demo(
        id=demo_id,
        path=path,
        name=_str(raw, "name", problems, required=True) or demo_id,
        customer=_str(raw, "customer", problems),
        summary=_str(raw, "summary", problems),
        surfaces=surfaces,
        scenarios=_strs(raw, "scenarios", problems),
        tag_scenarios=_strs(raw, "tag_scenarios", problems),
        trigger=_str(raw, "trigger", problems),
        channels=_strs(raw, "channels", problems),
        example=bool(raw.get("example", False)),
        files=_strs(raw, "files", problems),
        docs=tuple(docs),
        actions=tuple(actions),
        problems=tuple(problems),
    )


def load_demos(root: Path = ROOT) -> list[Demo]:
    """Every manifest, shared and private, customer demos first (by customer,
    then name), examples last."""
    paths = [*(root / "demos").glob("*.toml"), *(root / CUSTOM_DIR).glob(f"*/{MANIFEST_NAME}")]
    demos = [parse(p) for p in sorted(paths)]
    return sorted(demos, key=lambda d: (d.example, d.customer.lower(), d.name.lower()))


def get_demo(demo_id: str, root: Path = ROOT) -> Demo | None:
    return next((d for d in load_demos(root) if d.id == demo_id), None)


def owned_files(demo: Demo, root: Path = ROOT) -> set[Path]:
    """Every concrete file this demo owns (absolute): its manifest, its scenario
    modules, and its ``files`` entries (globs and directories expanded). A
    private demo also owns everything in its folder."""
    out = {demo.path.resolve()}
    if demo.folder:
        out.update(p.resolve() for p in demo.folder.rglob("*") if p.is_file())
    for slug in demo.scenarios:
        out.add((root / "scenarios" / f"{slug}.py").resolve())
    for slug in demo.tag_scenarios:
        out.add((root / "scenarios_tag" / f"{slug}.py").resolve())
    for pattern in demo.files:
        pattern = pattern.rstrip("/")
        direct = root / pattern
        matches = [direct] if direct.exists() else list(root.glob(pattern))
        for m in matches:
            if m.is_dir():
                out.update(p.resolve() for p in m.rglob("*") if p.is_file())
            else:
                out.add(m.resolve())
    return {p for p in out if p.exists()}
