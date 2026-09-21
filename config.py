"""Shared config for the Claude in Slack Demo Simulator: token loading, Slack client factories, chmod-600 guard."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from slack_sdk import WebClient

ROOT = Path(__file__).resolve().parent
TOKENS_PATH = ROOT / "tokens.json"

# Use www.slack.com (subdomain) instead of slack.com (apex) so requests pass
# through Claude Code's sandbox proxy, which allows *.slack.com but not the apex.
SLACK_BASE_URL = "https://www.slack.com/api/"


def _check_tokens_perms() -> None:
    """Refuse to load tokens.json if it's group/world-readable.

    tokens.json holds real Slack credentials — if anything else on the
    machine can read it, an attacker with any user account can impersonate
    the bot and any seeded persona. Set CLAUDE_DEMO_SKIP_TOKEN_PERM_CHECK=1
    to bypass during first-run setup before chmod 600 has been applied.
    """
    if os.environ.get("CLAUDE_DEMO_SKIP_TOKEN_PERM_CHECK") == "1":
        return
    mode = TOKENS_PATH.stat().st_mode
    if mode & 0o077:
        raise PermissionError(
            f"{TOKENS_PATH} is readable by group or other "
            f"(mode={oct(mode & 0o777)}). Run: chmod 600 {TOKENS_PATH}\n"
            f"To bypass once: CLAUDE_DEMO_SKIP_TOKEN_PERM_CHECK=1 <command>"
        )


def load_tokens() -> dict[str, Any]:
    _check_tokens_perms()
    with TOKENS_PATH.open() as f:
        return json.load(f)


def save_tokens(tokens: dict[str, Any]) -> None:
    """Write tokens.json atomically with owner-only permissions.

    Atomic: write to a sibling tmp file, then os.replace() — a crash mid-write
    can never leave tokens.json half-written. Permissions: 0600 so other users
    on the machine can't read the demo credentials. Applied before the
    rename so the final file is never briefly world-readable.
    """
    tmp = TOKENS_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(tokens, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKENS_PATH)


def _sole_or_first_bot(tokens: dict[str, Any]) -> dict[str, Any]:
    bots = tokens.get("bots", {})
    if not bots:
        raise RuntimeError(
            "No entries in tokens.json['bots']. Copy tokens.example.json to "
            "tokens.json and fill in your app's bot token."
        )
    return next(iter(bots.values()))


def app_client(app_id: str | None = None) -> WebClient:
    """Return a WebClient for the bot token.

    tokens.json['bots'] is a map keyed by Slack app id. With no app_id given,
    resolves the sole (or first) bot entry — the common case for this repo,
    which ships one bot. Pass app_id to pick a specific entry if you've added
    more than one.
    """
    tokens = load_tokens()
    bots = tokens.get("bots", {})
    if app_id is not None:
        bot = bots.get(app_id)
        if not bot:
            raise RuntimeError(f"No bot entry for app_id {app_id!r} in tokens.json['bots'].")
    else:
        bot = _sole_or_first_bot(tokens)
    return WebClient(token=bot["bot_token"], base_url=SLACK_BASE_URL)


# Alias for clarity where seed scripts want the bot's own identity.
agent_bot_client = app_client


def user_client(email: str) -> WebClient:
    """Return a WebClient bound to a persona's xoxp- user token, keyed by email.

    Always passes base_url=SLACK_BASE_URL so requests go through the sandbox
    proxy (which allowlists www.slack.com but not the apex). Raises if the
    email isn't an authorized persona in tokens.json['users'].
    """
    tokens = load_tokens()
    token = tokens.get("users", {}).get(email)
    if not token:
        raise RuntimeError(
            f"No user token for {email} in tokens.json['users']. "
            f"Add one before calling user_client({email!r})."
        )
    return WebClient(token=token, base_url=SLACK_BASE_URL)
