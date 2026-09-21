import json
import re

# A ```taskplan ``` fence carries a JSON object describing the steps the agent
# took (or will take), rendered as Slack's streaming "task card" timeline or
# "plan" block before the final answer. It mirrors the ```blockkit``` fence in
# blockkit.py — same extraction tiers, same bracket-walk recovery for responses
# that get cut off by max_tokens.
_STRICT_FENCE_RE = re.compile(r"^\s*```taskplan\s*\n(.*?)\n```\s*", re.DOTALL)
_LOOSE_FENCE_RE = re.compile(r"```taskplan\s*\n(.*?)\n```", re.DOTALL)

_ALLOWED_MODES = {"timeline", "plan"}
_ALLOWED_STATUSES = {"pending", "in_progress", "complete", "error"}

# Slack hard limits: task_update/plan_update chunks cap at 256 chars per field.
# We keep a margin so the bot never sends an over-length chunk that Slack rejects.
MAX_STEPS = 8
TITLE_MAX = 200
DETAILS_MAX = 200
OUTPUT_MAX = 200


def _coerce_plan(parsed) -> dict | None:
    """Accept either a bare {mode, steps, ...} object or {"plan": {...}}."""
    if isinstance(parsed, dict):
        if "plan" in parsed and isinstance(parsed["plan"], dict):
            return parsed["plan"]
        if "steps" in parsed or "mode" in parsed:
            return parsed
    return None


def _balanced_object_slice(s: str) -> str | None:
    """Return the substring from the first '{' to its matching '}', respecting
    quoted strings. Used to recover JSON when the closing ``` fence is missing
    (max_tokens cutoff) — the object analogue of blockkit's array slice."""
    start = s.find("{")
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
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
    return None


def extract_plan(text: str) -> tuple[dict | None, str]:
    """Pull a ```taskplan``` object off the front of a response.

    Returns (plan_or_None, rest). `rest` is the text with the taskplan fence
    removed, ready to flow into extract_blocks() / be streamed as prose. When no
    fence is present, returns (None, text) unchanged so existing behavior is
    byte-for-byte preserved.

    Unlike blockkit's tier string, the second element here is the *remaining
    text*, because a taskplan fence is a prefix to (not a replacement for) the
    answer. A malformed fence is treated as "no plan": we return (None, rest)
    and let the answer render normally rather than erroring the whole turn. When
    the fence is structurally present (regex-matched) but its JSON is bad, we
    still strip the fence span so the raw JSON never leaks into the reply.
    """
    m = _STRICT_FENCE_RE.match(text)
    if m:
        rest = text[m.end():].lstrip()
        return _try_parse_plan(m.group(1)), rest

    m = _LOOSE_FENCE_RE.search(text)
    if m:
        rest = (text[: m.start()] + text[m.end():]).strip()
        return _try_parse_plan(m.group(1)), rest

    if "```taskplan" in text:
        after = text.split("```taskplan", 1)[1]
        obj = _balanced_object_slice(after)
        if obj:
            plan = _try_parse_plan(obj)
            if plan is not None:
                # Truncated fence: there's no reliable "rest" (the answer was cut
                # off mid-plan). Render the plan, stream nothing else.
                return plan, ""
    return None, text


def _try_parse_plan(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return _coerce_plan(parsed)


def validate_plan(plan) -> list[str]:
    """Subset-check the plan against Slack's streaming chunk constraints.
    Returns a list of human-readable violations; empty list means "renderable".
    """
    violations: list[str] = []
    if not isinstance(plan, dict):
        return ["plan is not an object"]

    mode = plan.get("mode", "timeline")
    if mode not in _ALLOWED_MODES:
        violations.append(f"mode={mode!r} not in {sorted(_ALLOWED_MODES)}")

    title = plan.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > TITLE_MAX):
        violations.append(f"title must be a str <= {TITLE_MAX} chars")

    steps = plan.get("steps")
    if not isinstance(steps, list):
        violations.append("steps is not a list")
        return violations
    if not steps:
        violations.append("steps is empty")
    if len(steps) > MAX_STEPS:
        violations.append(f"too many steps: {len(steps)} (max {MAX_STEPS})")

    seen_ids: set = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            violations.append(f"step[{i}] is not an object")
            continue
        sid = step.get("id")
        if not isinstance(sid, str) or not sid:
            violations.append(f"step[{i}] missing non-empty string id")
        elif sid in seen_ids:
            violations.append(f"step[{i}] duplicate id {sid!r}")
        else:
            seen_ids.add(sid)

        st = step.get("title")
        if not isinstance(st, str) or not st:
            violations.append(f"step[{i}] missing non-empty title")
        elif len(st) > TITLE_MAX:
            violations.append(f"step[{i}] title >{TITLE_MAX} chars")

        for field, cap in (("details", DETAILS_MAX), ("output", OUTPUT_MAX)):
            val = step.get(field)
            if val is not None and (not isinstance(val, str) or len(val) > cap):
                violations.append(f"step[{i}] {field} must be a str <= {cap} chars")

        status = step.get("status")
        # isinstance guard first: `status not in {...}` raises TypeError on an
        # unhashable value (list/dict), which would escape validation entirely.
        if status is not None and (not isinstance(status, str) or status not in _ALLOWED_STATUSES):
            violations.append(f"step[{i}] status={status!r} not in {sorted(_ALLOWED_STATUSES)}")

        sources = step.get("sources")
        if sources is not None:
            if not isinstance(sources, list):
                violations.append(f"step[{i}] sources is not a list")
            else:
                for j, src in enumerate(sources):
                    if not isinstance(src, dict):
                        violations.append(f"step[{i}].sources[{j}] is not an object")
                        continue
                    url = src.get("url")
                    if not url or not isinstance(url, str):
                        violations.append(f"step[{i}].sources[{j}] missing string url")
                    txt = src.get("text")
                    # text is optional, but if present it must be a str —
                    # UrlSourceElement(text=<non-str>) serializes to invalid JSON.
                    if txt is not None and not isinstance(txt, str):
                        violations.append(f"step[{i}].sources[{j}] text must be a str")

    return violations
