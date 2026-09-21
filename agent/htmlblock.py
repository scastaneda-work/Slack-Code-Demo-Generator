"""Extract a fenced ```html``` artifact from an agent reply.

In a Slack Code channel the agent builds a live artifact (a self-contained HTML
file — a game, a landing page, a widget) and edits it across turns. We ask the
persona to emit the COMPLETE current file inside a ```html``` fence; this module
peels that fence off so the orchestrator can (a) publish the file to the Preview
tab and (b) diff it against the previous version for the Code tab. The prose that
surrounds the fence is the agent's chat reply.

Mirrors taskplan.py / blockkit.py: a strict leading-fence match first, a looser
embedded match next, and a tolerant recovery for a reply cut off by max_tokens
(an opening ```html with no closing fence). Zero cost when no fence is present —
returns (None, text) unchanged, exactly like extract_plan.

The fence is recognized either as an explicit ```html … ``` block or as a bare
``` … ``` block whose body opens with an HTML document marker (`<!doctype` or
`<html`), since a model sometimes drops the language tag.
"""

from __future__ import annotations

import re

# Explicit language-tagged fence at the very start of the reply.
_STRICT_HTML_RE = re.compile(r"^\s*```html\s*\n(.*?)\n```\s*", re.DOTALL | re.IGNORECASE)
# Explicit language-tagged fence anywhere in the reply.
_LOOSE_HTML_RE = re.compile(r"```html\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
# A bare ``` fence whose body opens with an HTML document marker (model dropped
# the `html` tag). Requires the doc marker so we never grab a non-HTML code block.
_BARE_DOC_RE = re.compile(
    r"```[a-zA-Z0-9]*\s*\n(\s*(?:<!doctype|<html)\b.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def _looks_like_html(s: str) -> bool:
    """A minimal self-containment sanity check: the body has angle-bracket markup
    and either a document marker or a tag. We do NOT rewrite or sanitize — the
    Preview view renders one string with no network, and the persona is told to
    emit a self-contained document; this only rejects obvious non-HTML."""
    if not s or "<" not in s or ">" not in s:
        return False
    return True


def extract_html(text: str) -> tuple[str | None, str]:
    """Pull a fenced HTML artifact off a reply.

    Returns (html_or_None, rest). ``rest`` is the reply with the HTML fence
    removed — the prose the agent posts in chat. When no HTML fence is present,
    returns (None, text) unchanged so a plain reply (a question, a follow-up with
    no code change) flows through untouched and the artifact tabs are left as-is.

    Recovery mirrors taskplan.extract_plan: strict leading fence → loose embedded
    fence → bare document fence → truncated (opening fence, no close). A fence
    that matches structurally but whose body isn't HTML-shaped is treated as "no
    artifact" (returns None) rather than publishing garbage to the Preview tab.
    """
    # 1) Strict: the reply opens with a ```html fence.
    m = _STRICT_HTML_RE.match(text)
    if m:
        body = m.group(1)
        rest = text[m.end():].lstrip()
        return (body if _looks_like_html(body) else None), (rest if _looks_like_html(body) else text)

    # 2) Loose: a ```html fence somewhere in the reply.
    m = _LOOSE_HTML_RE.search(text)
    if m:
        body = m.group(1)
        if _looks_like_html(body):
            rest = (text[: m.start()] + text[m.end():]).strip()
            return body, rest
        # structurally a fence but not HTML — leave the reply intact
        return None, text

    # 3) Bare ``` fence whose body is an HTML document (model dropped the tag).
    m = _BARE_DOC_RE.search(text)
    if m:
        body = m.group(1)
        rest = (text[: m.start()] + text[m.end():]).strip()
        return body, rest

    # 4) Truncated: an opening ```html with no closing fence (max_tokens cutoff).
    #    Take everything after the opener as the artifact; there's no reliable
    #    trailing prose, so rest is "".
    low = text.lower()
    idx = low.find("```html")
    if idx != -1:
        after = text[idx + len("```html"):]
        # drop an immediate newline after the tag
        after = after[1:] if after.startswith("\n") else after
        body = after.rsplit("```", 1)[0] if "```" in after else after
        body = body.strip("\n")
        if _looks_like_html(body):
            return body, ""

    return None, text
