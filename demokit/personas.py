"""Personas: which AI agent the bot presents as (Claude, GitHub Copilot, …).

One persona is active at a time — there's one bot. The choice lives in
``tokens.json["bots"][<app>]``: ``persona`` (the slug run.sh exports as
PERSONA) next to ``name`` (what Slack shows, pushed by run.sh at launch).
Applying a persona sets both, so the name and the voice never drift apart.
The app's picture is the one thing Slack won't let an app change — that stays a
manual upload in the app's settings (``app_settings_url``).

Only non-secret fields are written; the file keeps its chmod 600.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from agent.persona import DEFAULT_PERSONA, display_name, split_front_matter
from demokit.manifest import ROOT

TEMPLATE = DEFAULT_PERSONA  # new personas start from this file


@dataclass(frozen=True)
class Persona:
    slug: str
    name: str         # how it refers to itself ("Claude")
    slack_name: str   # what Slack shows ("Claude AI")


@dataclass(frozen=True)
class Current:
    slug: str          # the persona tokens.json selects (default if unset)
    slack_name: str    # the name Slack shows right now
    app_id: str

    def matches(self, p: Persona) -> bool:
        return p.slug == self.slug and p.slack_name == self.slack_name


def _dir(root: Path) -> Path:
    return root / "personas"


def _read(path: Path) -> Persona:
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    name = display_name(body)
    return Persona(slug=path.stem, name=name, slack_name=meta.get("slack_name") or name)


def list_personas(root: Path = ROOT) -> list[Persona]:
    paths = sorted(p for p in _dir(root).glob("*.md") if not p.name.startswith("_"))
    return sorted((_read(p) for p in paths), key=lambda p: (p.slug != DEFAULT_PERSONA, p.name.lower()))


def get(slug: str, root: Path = ROOT) -> Persona | None:
    path = _dir(root) / f"{slug}.md"
    return _read(path) if path.is_file() else None


def _tokens(root: Path) -> dict:
    return json.loads((root / "tokens.json").read_text())


def current(root: Path = ROOT) -> Current | None:
    """What tokens.json selects now, or None without a configured bot."""
    try:
        bots = _tokens(root).get("bots") or {}
    except (OSError, ValueError):
        return None
    if not bots:
        return None
    app_id, bot = next(iter(bots.items()))
    return Current(slug=bot.get("persona") or DEFAULT_PERSONA,
                   slack_name=bot.get("name") or "", app_id=app_id)


def apply(slug: str, root: Path = ROOT) -> Persona:
    """Make ``slug`` the active persona: tokens.json's persona + name. Takes
    effect when the bot (re)starts."""
    from config import load_tokens, save_tokens

    persona = get(slug, root)
    if persona is None:
        raise KeyError(f"no persona {slug!r} in personas/")
    path = root / "tokens.json"
    tokens = load_tokens(path)
    bots = tokens.get("bots") or {}
    if not bots:
        raise RuntimeError("tokens.json has no bot yet — finish SETUP.md first")
    bot = next(iter(bots.values()))
    bot["persona"] = persona.slug
    bot["name"] = persona.slack_name
    save_tokens(tokens, path)
    return persona


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "persona"


def create(name: str, maker: str, root: Path = ROOT) -> Persona:
    """A new persona from the default one with the identity swapped. Everything
    else in that file (Slack formatting, cards, checklists) is agent-neutral."""
    name, maker = name.strip(), maker.strip()
    if not name or not maker:
        raise ValueError("a persona needs a name and who makes it")
    slug = slugify(name)
    dest = _dir(root) / f"{slug}.md"
    if dest.exists():
        raise FileExistsError(f"there's already a persona called {name}")
    _, body = split_front_matter((_dir(root) / f"{TEMPLATE}.md").read_text(encoding="utf-8"))
    base = display_name(body)
    body = re.sub(rf"^#\s*{re.escape(base)}\b", f"# {name}", body, count=1)
    body = body.replace(f"You are *{base}*, an AI assistant made by Anthropic",
                        f"You are *{name}*, an AI assistant made by {maker}")
    body = body.replace(f"{base} is honest", f"{name} is honest")
    body = body.replace(f"signature-{base}", f"signature-{name}")
    dest.write_text(f"---\nslack_name: {name}\n---\n{body}", encoding="utf-8")
    return _read(dest)


def app_settings_url(app_id: str) -> str:
    """Where the SE uploads the app's picture (Slack has no API for it)."""
    return f"https://api.slack.com/apps/{app_id}/general"
