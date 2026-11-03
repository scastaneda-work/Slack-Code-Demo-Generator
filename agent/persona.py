"""Which AI agent the bot presents as — Claude by default, swappable.

A persona is one file, ``personas/<slug>.md``: the system prompt the live model
follows, under a small front-matter block naming what Slack shows::

    ---
    slack_name: Claude AI
    ---
    # Claude — Slack AI assistant
    You are *Claude*, …

The active slug comes from ``PERSONA`` (run.sh sets it from
``tokens.json["bots"][…]["persona"]``). Scripted, audience-facing lines that
name the agent ("Hey, I'm …", "Open session in …", sample chime-ins) read
``agent_name()`` so they follow the persona too. Stdlib only.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"
DEFAULT_PERSONA = "claude_ai"


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """``---``-fenced ``key: value`` lines at the top → (meta, the rest)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, text[end + 5:].lstrip("\n")


def display_name(body: str) -> str:
    """The name from the persona's ``# <Name> — …`` heading."""
    m = re.match(r"#\s*(.+?)\s+[—-]", body)
    return m.group(1).strip() if m else "Claude"


def active_slug() -> str:
    """The PERSONA env var if that persona exists, else the default."""
    slug = (os.environ.get("PERSONA") or "").strip()
    return slug if slug and (PERSONAS_DIR / f"{slug}.md").is_file() else DEFAULT_PERSONA


def load_persona(name: str | None = None) -> str:
    """The system prompt for ``name`` (default: the active persona), with the
    per-demo context include appended when present."""
    path = PERSONAS_DIR / f"{name or active_slug()}.md"
    _, prompt = split_front_matter(path.read_text(encoding="utf-8"))
    # Per-demo channel context (customer/cast/story) is an OPTIONAL include the
    # build-slack-demo skill writes to personas/_demo_context.md (gitignored).
    # Append it so the bot knows the demo backstory without editing the persona.
    demo_context = PERSONAS_DIR / "_demo_context.md"
    if demo_context.exists():
        prompt = prompt + "\n\n" + demo_context.read_text(encoding="utf-8")
    return prompt


@lru_cache(maxsize=None)
def agent_name() -> str:
    """How the agent refers to itself ("Claude"), for scripted lines."""
    _, body = split_front_matter((PERSONAS_DIR / f"{active_slug()}.md").read_text(encoding="utf-8"))
    return display_name(body)
