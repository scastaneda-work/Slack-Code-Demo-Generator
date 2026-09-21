"""Reference seed for the `website_redesign` Slack Code scenario (bot-spoofed).

Stages a short cross-functional backstory in a channel, posting every line
THROUGH THE BOT via chat:write.customize (each shows a name + optional avatar) —
no per-persona tokens needed. Ends with an @-mention that trips the Slack Code
intent gate and routes to website_redesign.

This is the REFERENCE the build-slack-demo skill mimics when it generates a seed
script for your own customer. WelloGuard + the cast are fictional samples —
swap them for your story.

Usage (from repo root, after SETUP.md):
    .venv/bin/python channels/seed_welloguard_redesign.py --create
    .venv/bin/python channels/seed_welloguard_redesign.py --channel C0XXXXXXXX
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import app_client, load_tokens  # noqa: E402

CHANNEL_NAME = "welloguard-redesign"
# Sample org number; only used to build best-effort persona-invite emails below.
ORG_NUM = os.environ.get("SLACK_DEMO_ORG_NUM", "7018")

# Fictional cast. icon_url optional — a real avatar URL renders the photo; omit
# for a neutral icon. email_stem drives an OPTIONAL best-effort invite of a real
# person if they happen to exist in your org (skipped silently otherwise).
CAST = [
    {"name": "Cindy Chen", "role": "Design lead", "email_stem": "cindy_central", "icon_url": None},
    {"name": "Adam Ferris", "role": "Frontend eng", "email_stem": "adam_ferris", "icon_url": None},
    {"name": "Lauren Bailey", "role": "Product", "email_stem": "lauren_bailey", "icon_url": None},
]

# (sender_name, icon_url, text, is_parent). A believable design/eng/product thread
# that plants the white-on-amber CTA contrast concern, ending with the trigger
# mention ("<@BOT>" is replaced with the real bot user id at runtime).
MESSAGES = [
    ("Cindy Chen", None,
     "Homepage redesign is ready for a build — single hero, one strong CTA, and the "
     "conversion copy is locked. Want it live before the noon launch. :art:", True),
    ("Lauren Bailey", None,
     "Love it. This is the version we aligned on in the review — clean hero, one primary action.", False),
    ("Adam Ferris", None,
     "On it. One flag before we ship: the CTA is white text on the amber `#F0C808` "
     "button — let's double-check it's actually readable, that contrast looks thin to me.", False),
    ("Cindy Chen", None,
     "Good catch — worth a quick check. Either way we want it live today.", False),
    ("Lauren Bailey", None,
     "Agreed. <@BOT> implement the homepage redesign on welloguard/marketing-site — "
     "ship before noon, and flag anything off with the CTA contrast.", False),
]


def _post(client: WebClient, channel: str, name: str, icon_url, text: str, thread_ts=None):
    """Post one line THROUGH THE BOT, spoofing `name` via chat:write.customize."""
    kwargs = {"channel": channel, "text": text, "username": name}
    if icon_url:
        kwargs["icon_url"] = icon_url
    else:
        kwargs["icon_emoji"] = ":bust_in_silhouette:"  # neutral human icon beats the bot avatar
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    return client.chat_postMessage(**kwargs)


def get_or_create_channel(client: WebClient, channel_id, name: str) -> str:
    if channel_id:
        return channel_id
    try:
        resp = client.conversations_create(name=name)
        return resp["channel"]["id"]
    except SlackApiError:
        # name_taken (or similar) — find the existing channel by name.
        cursor = None
        while True:
            page = client.conversations_list(types="public_channel", limit=1000, cursor=cursor)
            for c in page["channels"]:
                if c["name"] == name:
                    return c["id"]
            cursor = page.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        raise


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the website_redesign Slack Code backstory (bot-spoofed).")
    ap.add_argument("--channel", help="Existing channel id to seed into.")
    ap.add_argument("--create", action="store_true", help="Create/reuse the channel by name.")
    ap.add_argument(
        "--invite",
        action="store_true",
        help="ALSO try to invite the real people in CAST to the channel roster "
        "(by email). OFF by default — the scripted voices carry the demo on their "
        "own. Turn it on only if the CAST email stems match real users in your org; "
        "any that don't resolve are skipped silently.",
    )
    args = ap.parse_args()

    client = app_client()
    tokens = load_tokens()
    bots = tokens.get("bots", {})
    bot = next(iter(bots.values()), {})
    bot_user_id = bot.get("bot_user_id", "")

    channel = get_or_create_channel(client, args.channel, CHANNEL_NAME)

    # Bot joins so it can post + be @mentioned.
    try:
        client.conversations_join(channel=channel)
    except SlackApiError:
        pass

    # OPTIONAL, opt-in (--invite): try to add the real people to the channel roster
    # by email. Off by default — the scripted voices carry the demo without it. Any
    # address that doesn't resolve to a real user is skipped silently.
    if not args.invite:
        print("[invite] skipped (pass --invite to try adding the real cast to the roster).")
    for m in CAST if args.invite else []:
        email = f"demoeng+{m['email_stem']}_{ORG_NUM}@slack-corp.com"
        try:
            u = client.users_lookupByEmail(email=email)
            client.conversations_invite(channel=channel, users=u["user"]["id"])
            print(f"[invite] {m['name']} <{email}>")
        except SlackApiError:
            pass  # a fresh org won't have these personas — that's fine

    mention = f"<@{bot_user_id}>" if bot_user_id else "@Claude AI"
    parent_ts = None
    for name, icon, text, is_parent in MESSAGES:
        text = text.replace("<@BOT>", mention)
        r = _post(client, channel, name, icon, text, thread_ts=None if is_parent else parent_ts)
        if is_parent:
            parent_ts = r["ts"]
        time.sleep(0.5)

    print(f"[seed] staged '{CHANNEL_NAME}' ({channel}). Mention the bot in the thread to open the Slack Code channel.")


if __name__ == "__main__":
    main()
