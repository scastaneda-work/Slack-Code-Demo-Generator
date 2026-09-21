import json
import re

_STRICT_FENCE_RE = re.compile(r"^\s*```blockkit\s*\n(.*?)\n```\s*$", re.DOTALL)
_LOOSE_FENCE_RE = re.compile(r"```blockkit\s*\n(.*?)\n```", re.DOTALL)

_ALLOWED_BLOCK_TYPES = {"header", "divider", "section", "context", "actions"}
_ALLOWED_ELEMENT_TYPES = {"mrkdwn", "plain_text", "image", "button"}


def _coerce_blocks(parsed) -> list | None:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        blocks = parsed.get("blocks")
        if isinstance(blocks, list):
            return blocks
    return None


def _balanced_array_slice(s: str) -> str | None:
    """Return the substring from the first '[' to its matching ']', respecting quoted strings.
    Used to recover JSON when the closing ``` fence is missing (max_tokens cutoff)."""
    start = s.find("[")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
    return None


def extract_blocks(text: str) -> tuple[list | None, str]:
    """Pull a Block Kit blocks list from text. Returns (blocks_or_None, tier).

    Tier values:
      - "strict"         — exact ```blockkit…``` fence, response is only the fence
      - "trailing_prose" — fence present but extra text before/after
      - "truncated"      — no closing fence; bracket-walked the JSON array
      - "parse_error"    — extraction found something but JSON parse failed
      - "no_fence"       — no ```blockkit marker at all
    """
    m = _STRICT_FENCE_RE.match(text)
    if m:
        try:
            parsed = json.loads(m.group(1))
        except ValueError:
            return None, "parse_error"
        blocks = _coerce_blocks(parsed)
        if blocks is not None:
            return blocks, "strict"
        return None, "parse_error"

    m = _LOOSE_FENCE_RE.search(text)
    if m:
        try:
            parsed = json.loads(m.group(1))
        except ValueError:
            return None, "parse_error"
        blocks = _coerce_blocks(parsed)
        if blocks is not None:
            return blocks, "trailing_prose"
        return None, "parse_error"

    if "```blockkit" in text:
        after = text.split("```blockkit", 1)[1]
        arr = _balanced_array_slice(after)
        if arr:
            try:
                parsed = json.loads(arr)
            except ValueError:
                return None, "parse_error"
            if isinstance(parsed, list):
                return parsed, "truncated"
        return None, "parse_error"

    return None, "no_fence"


def validate_blocks(blocks) -> list[str]:
    """Subset-check Slack's Block Kit constraints. Returns a list of human-readable
    violations; empty list means "looks postable."""
    violations: list[str] = []
    if not isinstance(blocks, list):
        return ["top-level is not a list"]
    if len(blocks) > 50:
        violations.append(f"too many blocks: {len(blocks)} (max 50)")
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            violations.append(f"block[{i}] is not an object")
            continue
        t = b.get("type")
        if t not in _ALLOWED_BLOCK_TYPES:
            violations.append(f"block[{i}].type={t!r} not in allowed set")
            continue
        if t == "header":
            txt = b.get("text") or {}
            if txt.get("type") != "plain_text":
                violations.append(f"block[{i}] header.text.type must be plain_text")
            if len(txt.get("text", "")) > 150:
                violations.append(f"block[{i}] header.text >150 chars")
        elif t == "section":
            txt = b.get("text")
            if not isinstance(txt, dict):
                violations.append(f"block[{i}] section missing text object")
            else:
                if txt.get("type") not in {"mrkdwn", "plain_text"}:
                    violations.append(f"block[{i}] section.text.type invalid")
                if len(txt.get("text", "")) > 3000:
                    violations.append(f"block[{i}] section.text >3000 chars")
        elif t == "context":
            els = b.get("elements")
            if not isinstance(els, list):
                violations.append(f"block[{i}] context.elements missing")
            elif len(els) > 10:
                violations.append(f"block[{i}] context.elements >10")
            else:
                for j, el in enumerate(els):
                    if not isinstance(el, dict) or el.get("type") not in _ALLOWED_ELEMENT_TYPES:
                        violations.append(f"block[{i}].elements[{j}].type invalid")
    return violations
