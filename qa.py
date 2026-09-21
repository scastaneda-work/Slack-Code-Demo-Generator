"""Pre-demo QA harness for claude-ai-bot.

Two modes:

  --self-test       Offline fixture tests for the Block Kit extractor and
                    validator. No network, sub-second.

  --vet-questions   Live: load examples/demo_questions.md, fire each at
                    run_agent() (skipping Slack), check for Block Kit
                    parseability when expected, schema cleanliness, and
                    absence of character-break markers. Exit non-zero on
                    any FAIL.

Usage (from claude-ai-bot/):

  python qa.py --self-test
  python qa.py --vet-questions
  python qa.py --vet-questions --questions "q1|q2|q3"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from agent.agent import run_agent  # noqa: E402
from agent.blockkit import extract_blocks, validate_blocks  # noqa: E402
from agent.taskplan import extract_plan, validate_plan  # noqa: E402

CHAR_BREAK_MARKERS = [
    "i don't have access",
    "i can't tell",
    "who are you",
    "what's your name",
    "as an ai",
    "i'm an ai assistant",
    "i don't know who you are",
    "could you tell me your name",
    "i'm not sure who you",
    "i am an ai language model",
    "as claude",
]

DEMO_QUESTIONS_PATH = _HERE / "examples" / "demo_questions.md"


def _load_demo_questions() -> list[str]:
    if not DEMO_QUESTIONS_PATH.exists():
        return []
    out: list[str] = []
    for raw in DEMO_QUESTIONS_PATH.read_text().splitlines():
        line = raw.strip()
        if not line.startswith(("- ", "* ")):
            continue
        q = line[2:].strip()
        if len(q) >= 2 and q[0] in "\"'" and q[-1] == q[0]:
            q = q[1:-1].strip()
        if q:
            out.append(q)
    return out


def _run_self_test() -> int:
    fail = 0

    # Skip the fast-first-paint spinner dwell in _run_session_body so the offline
    # suite doesn't sleep ~3s per scripted-session fixture. (0 is a valid dwell.)
    import os as _os
    _os.environ.setdefault("SLACK_CODE_PREBUILT_DWELL_S", "0")

    extractor_fixtures: list[tuple[str, str, str, bool]] = [
        # (label, text, expected_tier, expects_blocks)
        (
            "strict",
            '```blockkit\n[{"type":"divider"}]\n```',
            "strict",
            True,
        ),
        (
            "trailing_prose",
            'Sure, here you go:\n```blockkit\n[{"type":"divider"}]\n```\nLet me know!',
            "trailing_prose",
            True,
        ),
        (
            "truncated",
            '```blockkit\n[{"type":"divider"},{"type":"divider"}]',
            "truncated",
            True,
        ),
        (
            "parse_error",
            '```blockkit\n[not json]\n```',
            "parse_error",
            False,
        ),
        (
            "no_fence",
            "Just a plain text reply, no fence at all.",
            "no_fence",
            False,
        ),
    ]
    for label, text, expected_tier, expects_blocks in extractor_fixtures:
        blocks, tier = extract_blocks(text)
        ok_tier = tier == expected_tier
        ok_blocks = (blocks is not None) == expects_blocks
        status = "PASS" if (ok_tier and ok_blocks) else "FAIL"
        if status == "FAIL":
            fail += 1
        print(f"[{status}] extract_blocks/{label} tier={tier} blocks_present={blocks is not None}")

    validator_fixtures: list[tuple[str, list, bool]] = [
        # (label, blocks, expects_violations)
        ("valid card", [
            {"type": "header", "text": {"type": "plain_text", "text": "Hi"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "ok"}},
        ], False),
        ("oversize section", [
            {"type": "section", "text": {"type": "mrkdwn", "text": "x" * 3001}},
        ], True),
        ("bad type", [{"type": "footer"}], True),
        ("missing section text", [{"type": "section"}], True),
        ("not a list", "not a list", True),
    ]
    for label, blocks, expects in validator_fixtures:
        violations = validate_blocks(blocks)
        ok = bool(violations) == expects
        status = "PASS" if ok else "FAIL"
        if status == "FAIL":
            fail += 1
        print(f"[{status}] validate_blocks/{label} violations={len(violations)}")

    # ---- taskplan (optional streaming task-card capability) ----------------
    plan_fixtures = [
        ("plan_then_blockkit",
         '```taskplan\n{"mode":"timeline","steps":[{"id":"1","title":"Did a thing"}]}\n```\n'
         '```blockkit\n[{"type":"divider"}]\n```', True, "```blockkit"),
        ("no_fence", "Just a plain reply, no taskplan.", False, "Just a plain reply, no taskplan."),
        ("malformed_stripped", "```taskplan\n{not json}\n```\nanswer body", False, "answer body"),
    ]
    for label, text, expects_plan, rest_startswith in plan_fixtures:
        plan, rest = extract_plan(text)
        ok = (plan is not None) == expects_plan and rest.startswith(rest_startswith)
        status = "PASS" if ok else "FAIL"
        if status == "FAIL":
            fail += 1
        print(f"[{status}] extract_plan/{label} plan_present={plan is not None}")

    plan_validator_fixtures = [
        ("valid timeline", {"mode": "timeline", "steps": [{"id": "1", "title": "Step one"}]}, False),
        ("bad mode", {"mode": "nope", "steps": [{"id": "1", "title": "x"}]}, True),
        ("dup ids", {"mode": "timeline", "steps": [{"id": "1", "title": "a"}, {"id": "1", "title": "b"}]}, True),
        ("empty steps", {"mode": "timeline", "steps": []}, True),
    ]
    for label, plan, expects in plan_validator_fixtures:
        violations = validate_plan(plan)
        ok = bool(violations) == expects
        status = "PASS" if ok else "FAIL"
        if status == "FAIL":
            fail += 1
        print(f"[{status}] validate_plan/{label} violations={len(violations)}")

    # ---- Slack Code (gated coding-channel capability) ----------------------
    from agent.slackcode import (
        SlackCodeConfig,
        _clean_context_item,
        channel_name_for,
        is_coding_task,
    )
    from listeners.events.slack_code import render_plan_as_text

    def _sanitize_bar(items):
        return [c for c in (_clean_context_item(it) for it in items[:5]) if c]

    off = SlackCodeConfig(enabled=False)
    on = SlackCodeConfig(enabled=True)
    intent_fixtures = [
        # (label, text, cfg, expected)
        ("disabled gate is off", "migrate the billing cron", off, False),
        ("coding task when on", "migrate the billing cron to Temporal", on, True),
        ("open-a-pr when on", "open a PR to fix the flaky login test", on, True),
        ("chat question when on", "what did we decide in this thread?", on, False),
        ("catch-up is not coding", "catch me up on what's still open here", on, False),
    ]
    for label, text, cfg, expected in intent_fixtures:
        got = is_coding_task(text, cfg)
        ok = got == expected
        status = "PASS" if ok else "FAIL"
        if status == "FAIL":
            fail += 1
        print(f"[{status}] slackcode.is_coding_task/{label} -> {got}")

    # channel_name_for: slugged, prefixed, <=80
    cn = channel_name_for("Migrate the Billing Cron to Temporal!!!", on)
    cn_ok = cn.startswith("claude-") and cn == cn.lower() and len(cn) <= 80 and " " not in cn
    print(f"[{'PASS' if cn_ok else 'FAIL'}] slackcode.channel_name_for -> {cn}")
    if not cn_ok:
        fail += 1

    # context-bar sanitize: caps at 5, drops bad icons, keeps valid ones
    raw_items = [{"key": f"k{i}", "label": f"L{i}", "icon": "folder"} for i in range(7)]
    raw_items.append({"key": "bad", "label": "bad", "icon": "not-an-icon"})
    clean = _sanitize_bar(raw_items)
    bar_ok = len(clean) == 5 and all("icon" in c for c in clean[:5])
    print(f"[{'PASS' if bar_ok else 'FAIL'}] slackcode._clean_context_item(bar) -> {len(clean)} items")
    if not bar_ok:
        fail += 1

    # bad icon dropped but item kept
    one = _sanitize_bar([{"key": "k", "label": "L", "icon": "nope"}])
    icon_ok = len(one) == 1 and "icon" not in one[0]
    print(f"[{'PASS' if icon_ok else 'FAIL'}] slackcode.sanitize/bad-icon-dropped -> {one}")
    if not icon_ok:
        fail += 1

    # _build_context_bar PROGRESSES through the story arc via `phase`, adding/
    # removing chips to track where the work stands (no fixed count). Link chips
    # (info + url) for orientation, plus ONE item_type:"action" chip ("Create PR")
    # from the working phase on — whose click should deliver a code_channel_action.
    from listeners.events.slack_code import _build_context_bar

    _cfg_bar = SlackCodeConfig(enabled=True)

    def _keys(phase):
        return [i["key"] for i in _build_context_bar(_cfg_bar, branch="feat/x", phase=phase)]

    phase_ok = (
        _keys("open") == ["repo", "branch"]
        and _keys("working") == ["repo", "branch", "pr", "create-pr"]
        and _keys("checking") == ["repo", "branch", "pr", "ci", "create-pr"]
        and _keys("passed") == ["repo", "branch", "pr", "check", "create-pr"]
        and all(len(_keys(ph)) <= 5 for ph in ("open", "working", "checking", "passed"))  # doc's ≤5 cap
    )
    print(f"[{'PASS' if phase_ok else 'FAIL'}] slack_code._build_context_bar/phases open|working|checking|passed")
    if not phase_ok:
        fail += 1

    # Exactly ONE action chip (create-pr), and it's an action (no url); every OTHER
    # chip is a link (has a url). CI flips failing → the green check gate between
    # checking and passed. 'open' stays orientation-only (no action chip yet).
    _open = {i["key"]: i for i in _build_context_bar(_cfg_bar, branch="feat/x", phase="open")}
    _checking = {i["key"]: i for i in _build_context_bar(_cfg_bar, branch="feat/x", phase="checking")}
    _passed = {i["key"]: i for i in _build_context_bar(_cfg_bar, branch="feat/x", phase="passed", check_label="durability")}
    def _action_chips(ph):
        return [i for i in _build_context_bar(_cfg_bar, branch="feat/x", phase=ph) if i.get("item_type") == "action"]
    chips_shape_ok = (
        _action_chips("open") == []                                                      # orientation only
        and all(len(_action_chips(ph)) == 1 and _action_chips(ph)[0]["key"] == "create-pr"
                and "url" not in _action_chips(ph)[0]                                     # action chip has NO url
                for ph in ("working", "checking", "passed"))
        and all(i.get("url") for ph in ("open", "working", "checking", "passed")
                for i in _build_context_bar(_cfg_bar, branch="feat/x", phase=ph)
                if i.get("item_type") != "action")                                       # every non-action chip is a link
        and _checking["ci"]["label"] == "CI: failing" and _checking["ci"].get("url")     # CI: failing during check
        and _passed["check"]["label"] == "durability: pass" and _passed["check"].get("url")  # green gate after patch
        and "ci" not in _passed and "check" not in _checking                             # they swap, not stack
    )
    print(f"[{'PASS' if chips_shape_ok else 'FAIL'}] slack_code._build_context_bar/one-action-chip+ci-flips")
    if not chips_shape_ok:
        fail += 1

    # is_code_channel result cache: is_code_channel runs on the hot follow-up path
    # (every non-mention message), so its verdict is cached per channel_id to avoid
    # a conversations.info per message. True is permanent; False has a short TTL; an
    # API error is NOT cached; mark_code_channel seeds True with no API; the
    # view-probe passes use_cache=False. Guard all of that.
    import asyncio as _asyncio_cc
    import agent.slackcode as slackcode  # module handle (qa.py imports names, not the module)
    from slack_sdk.errors import SlackApiError as _SAE

    class _CCClient:
        def __init__(self, is_cc, raise_err=False):
            self.calls = 0
            self._is_cc = is_cc
            self._raise = raise_err
        async def auth_test(self):
            return {"team_id": "T123", "user_id": "U999"}
        async def conversations_info(self, **kw):
            self.calls += 1
            if self._raise:
                raise _SAE("boom", {"error": "ratelimited"})
            rec = {"record_type": "agent_channel"} if self._is_cc else {}
            return {"channel": {"properties": {"record_channel": rec}}}

    async def _cc_cache_checks():
        results = {}
        # True cached permanently → 1 API hit for 2 calls.
        slackcode.clear_code_channel_cache()
        slackcode._BOT_IDENTITY = None
        c = _CCClient(is_cc=True)
        r1 = await slackcode.is_code_channel(c, "C_CODE")
        r2 = await slackcode.is_code_channel(c, "C_CODE")
        results["true_cached"] = (r1 is True and r2 is True and c.calls == 1)
        # False cached within TTL → 1 API hit for 2 calls.
        slackcode.clear_code_channel_cache()
        c2 = _CCClient(is_cc=False)
        r3 = await slackcode.is_code_channel(c2, "C_PLAIN")
        r4 = await slackcode.is_code_channel(c2, "C_PLAIN")
        results["false_cached"] = (r3 is False and r4 is False and c2.calls == 1)
        # False re-checked after the TTL expires → 2 API hits.
        slackcode.clear_code_channel_cache()
        _orig = slackcode._CODE_CHANNEL_NEG_TTL_S
        slackcode._CODE_CHANNEL_NEG_TTL_S = 0.05
        c3 = _CCClient(is_cc=False)
        await slackcode.is_code_channel(c3, "C_NEW")
        await _asyncio_cc.sleep(0.1)
        await slackcode.is_code_channel(c3, "C_NEW")
        slackcode._CODE_CHANNEL_NEG_TTL_S = _orig
        results["false_rechecked"] = (c3.calls == 2)
        # mark_code_channel seeds True with no API call.
        slackcode.clear_code_channel_cache()
        c4 = _CCClient(is_cc=True)
        slackcode.mark_code_channel("C_SEEDED")
        r5 = await slackcode.is_code_channel(c4, "C_SEEDED")
        results["seed_no_api"] = (r5 is True and c4.calls == 0)
        # use_cache=False bypasses the cache (the probe path).
        slackcode.clear_code_channel_cache()
        c5 = _CCClient(is_cc=True)
        slackcode.mark_code_channel("C_PROBE")
        await slackcode.is_code_channel(c5, "C_PROBE", use_cache=False)
        results["nocache_bypasses"] = (c5.calls == 1)
        # An API error is not cached → the next message retries.
        slackcode.clear_code_channel_cache()
        c6 = _CCClient(is_cc=False, raise_err=True)
        await slackcode.is_code_channel(c6, "C_ERR")
        await slackcode.is_code_channel(c6, "C_ERR")
        results["error_not_cached"] = (c6.calls == 2)
        slackcode.clear_code_channel_cache()
        return results

    _cc = _asyncio_cc.run(_cc_cache_checks())
    cc_cache_ok = all(_cc.values())
    print(f"[{'PASS' if cc_cache_ok else 'FAIL'}] slackcode.is_code_channel/result-cache "
          f"({', '.join(k for k, v in _cc.items() if not v) or 'all'})")
    if not cc_cache_ok:
        fail += 1

    # Participant invite + resolve: org-num derived from auth.test url, persona email
    # built as demoeng+<stem>_<orgnum>@…, resolved via lookupByEmail (→ uid + avatar),
    # invited. Returns a status string. Best-effort: unknown org / lookup failure →
    # skip (no invite), never raises.
    def _reset_participant_state():
        slackcode._DEMO_ORG_NUM = None
        slackcode._PARTICIPANT_CACHE.clear()
        slackcode._WORKSPACE_TEAM_ID = None
        import os as _os0
        _os0.environ.pop("SLACK_WORKSPACE_TEAM_ID", None)

    class _InvClient:
        # resolve=True: lookupByEmail succeeds. resolve=False/raise_lookup=True:
        # lookupByEmail fails; then resolution falls back to users.list. Set
        # list_resolve=False to also make the users.list fallback miss.
        def __init__(self, url="https://slack-demo-7018.enterprise.slack.com/", resolve=True,
                     raise_lookup=False, list_resolve=False):
            self._url = url
            self._resolve = resolve
            self._raise = raise_lookup
            self._list_resolve = list_resolve
            self.looked_up: list[str] = []
            self.listed = 0
            self.invited: list[str] = []
        async def auth_test(self):
            return {"url": self._url, "team_id": "T1", "user_id": "U1"}
        async def users_lookupByEmail(self, *, email, team_id=None):
            self.looked_up.append(email)
            if self._raise or not self._resolve:
                raise _SAE("nope", {"error": "users_not_found"})
            stem = email.split("+", 1)[1].rsplit("_", 1)[0]
            return {"ok": True, "user": {"id": "U_" + stem, "profile": {
                "image_192": f"https://avatars/{stem}_192.png", "real_name": stem.replace("_", " ").title()}}}
        async def users_list(self, *, limit=200, cursor=None, team_id=None):
            self.listed += 1
            if not self._list_resolve:
                return {"ok": True, "members": [], "response_metadata": {"next_cursor": ""}}
            # Model the org directory keyed by profile.email (Grid-safe fallback path).
            members = [{"id": "U_" + s, "profile": {"email": f"demoeng+{s}_7018@slack-corp.com",
                        "image_192": f"https://avatars/{s}_192.png", "real_name": s.replace('_', ' ').title()}}
                       for s in ("ralph_clark", "elliott_executive")]
            return {"ok": True, "members": members, "response_metadata": {"next_cursor": ""}}
        async def conversations_invite(self, *, channel, users):
            self.invited.extend(users.split(","))
            return {"ok": True}

    async def _invite_checks():
        r = {}
        import os as _os
        _reset_participant_state()
        _os.environ.pop("SLACK_DEMO_ORG_NUM", None)
        # org-num derived from the url
        c = _InvClient()
        assert await slackcode._demo_org_num(c) == "7018"
        r["org_from_url"] = True
        # email construction
        r["email_built"] = slackcode._persona_email("adam_ferris", "7018") == "demoeng+adam_ferris_7018@slack-corp.com"
        # resolve_participant returns uid + icon_url
        _reset_participant_state()
        cr = _InvClient()
        info = await slackcode.resolve_participant(cr, "ralph_clark")
        r["resolve_uid_and_icon"] = bool(info and info.get("user_id") == "U_ralph_clark"
                                         and info.get("icon_url", "").endswith("ralph_clark_192.png"))
        # happy path: 2 stems → 2 lookups → 2 invites, status "invited 2/2"
        _reset_participant_state()
        c2 = _InvClient()
        st2 = await slackcode.invite_participants(c2, "C1", ["ralph_clark", "elliott_executive"])
        r["lookup_and_invite"] = (
            c2.looked_up == ["demoeng+ralph_clark_7018@slack-corp.com", "demoeng+elliott_executive_7018@slack-corp.com"]
            and len(c2.invited) == 2 and st2 == "invited 2/2")
        # env override wins over url
        _reset_participant_state()
        _os.environ["SLACK_DEMO_ORG_NUM"] = "9999"
        c3 = _InvClient(url="https://no-number-here.slack.com/")
        await slackcode.invite_participants(c3, "C1", ["ralph_clark"])
        r["env_override"] = c3.looked_up == ["demoeng+ralph_clark_9999@slack-corp.com"]
        _os.environ.pop("SLACK_DEMO_ORG_NUM", None)
        # lookup fails AND users.list fallback misses → no invite, status includes error
        _reset_participant_state()
        c4 = _InvClient(raise_lookup=True, list_resolve=False)
        st4 = await slackcode.invite_participants(c4, "C1", ["ralph_clark"])
        r["lookup_fail_skips"] = (c4.invited == [] and c4.listed >= 1 and st4.startswith("0/1 resolved"))
        # lookup fails BUT users.list fallback resolves → invited via fallback
        _reset_participant_state()
        c4b = _InvClient(raise_lookup=True, list_resolve=True)
        st4b = await slackcode.invite_participants(c4b, "C1", ["ralph_clark", "elliott_executive"])
        r["users_list_fallback"] = (len(c4b.invited) == 2 and c4b.listed >= 1 and st4b == "invited 2/2")
        # unknown org (no url number, no env) → skip entirely (no lookup), status "org-num unknown"
        _reset_participant_state()
        c5 = _InvClient(url="https://plain.slack.com/")
        st5 = await slackcode.invite_participants(c5, "C1", ["ralph_clark"])
        r["no_org_skips"] = c5.looked_up == [] and st5 == "org-num unknown"
        # empty list → no-op
        _reset_participant_state()
        c6 = _InvClient()
        await slackcode.invite_participants(c6, "C1", [])
        r["empty_noop"] = c6.looked_up == [] and c6.invited == []
        slackcode._DEMO_ORG_NUM = None
        return r

    _inv = _asyncio_cc.run(_invite_checks())
    inv_ok = all(_inv.values())
    print(f"[{'PASS' if inv_ok else 'FAIL'}] slackcode.invite_participants/by-email "
          f"({', '.join(k for k, v in _inv.items() if not v) or 'all'})")
    if not inv_ok:
        fail += 1

    # Scenario-aware chips: each story's repo/branch/PR chips must reflect ITS OWN
    # identity (checkout_incident → acme/checkout-service, hotfix/, PR #914), not
    # the shared config default. Guards the branch/repo/PR-matches-the-story fix.
    from listeners.events.slack_code import _branch_for, _branch_prefix, _pr_label, _repo_facts
    from scenarios import load_scenario as _load_sc

    _ci = _load_sc("checkout_incident")
    _repo, _repo_url, _pr = _repo_facts(_cfg_bar, _ci)
    _bar_ci = _build_context_bar(_cfg_bar, branch="hotfix/x", phase="working", scenario=_ci)
    _ci_by_key = {i["key"]: i for i in _bar_ci}
    scen_chips_ok = (
        _repo == "acme/checkout-service"
        and _pr.endswith("/pull/914")
        and _pr_label(_pr) == "PR #914 (draft)"
        and _branch_prefix(_cfg_bar, _ci) == "hotfix/"
        and _ci_by_key["repo"]["label"] == "acme/checkout-service"
        and _ci_by_key["pr"]["label"] == "PR #914 (draft)"
        # _branch_for reconstructs from channel name using the scenario prefix:
        and _branch_for(_cfg_bar, "claude-roll-back-the-retry-storm", _ci).startswith("hotfix/")
    )
    print(f"[{'PASS' if scen_chips_ok else 'FAIL'}] slack_code.context-bar/chips-match-scenario (repo={_repo}, pr={_pr_label(_pr)})")
    if not scen_chips_ok:
        fail += 1

    # Freeform (neutral sandbox, empty PR) falls back to cfg defaults gracefully.
    _ff = _load_sc("freeform")
    _bar_ff = _build_context_bar(_cfg_bar, branch="master", phase="working", scenario=_ff)
    _ff_by_key = {i["key"]: i for i in _bar_ff}
    ff_chips_ok = bool(_ff_by_key["repo"]["label"]) and "pr" in _ff_by_key  # present, no crash on empty PR_URL
    print(f"[{'PASS' if ff_chips_ok else 'FAIL'}] slack_code.context-bar/freeform-fallback -> repo={_ff_by_key['repo']['label']!r}")
    if not ff_chips_ok:
        fail += 1

    # ---- Slack Code capability guardrails (Phase 2) ------------------------
    # setCommands cap/name guard: strip leading slash, drop builtins + dups,
    # 1-31 chars, cap at MAX_COMMANDS. Turns a whole-set rejection into a clean set.
    from agent.slackcode import (
        MAX_COMMANDS,
        MAX_VIEWS,
        _clean_agent_resource,
        _clean_commands,
        _clean_summary_message,
        _views_from,
    )
    from agent.slackcode import SlackCodeResult as _SCR

    raw_cmds = [
        {"name": "/create-pr", "description": "open a PR", "argument_hint": "[title]"},  # slash stripped
        {"name": "run-tests", "description": "run tests"},
        {"name": "run-tests", "description": "dup dropped"},                               # dup
        {"name": "archive", "description": "builtin dropped"},                             # builtin collision
        {"name": "", "description": "empty dropped"},                                      # too short
        {"name": "x" * 32, "description": "too long dropped"},                             # too long
        {"name": "should-escape", "description": "keeps flag", "should_escape": True},
        {"not": "a name"},                                                                 # malformed dropped
    ]
    cleaned = _clean_commands(raw_cmds)
    names = [c["name"] for c in cleaned]
    cmd_guard_ok = (
        "create-pr" in names and names.count("run-tests") == 1
        and "archive" not in names and "" not in names and ("x" * 32) not in names
        and all(1 <= len(n) <= 31 for n in names)
        and any(c.get("should_escape") is True for c in cleaned)
        and cleaned[0] == {"name": "create-pr", "description": "open a PR", "argument_hint": "[title]"}
    )
    print(f"[{'PASS' if cmd_guard_ok else 'FAIL'}] slackcode._clean_commands -> {names}")
    if not cmd_guard_ok:
        fail += 1

    # cap at MAX_COMMANDS
    capped = _clean_commands([{"name": f"cmd-{i}"} for i in range(MAX_COMMANDS + 5)])
    cap_ok = len(capped) == MAX_COMMANDS
    print(f"[{'PASS' if cap_ok else 'FAIL'}] slackcode._clean_commands/cap -> {len(capped)} (max {MAX_COMMANDS})")
    if not cap_ok:
        fail += 1

    # summary_message + agent_resource cleaners keep only documented fields.
    sm = _clean_summary_message({"message_ts": "1.2", "thread_ts": "0.9", "junk": "x"})
    sm_ok = sm == {"message_ts": "1.2", "thread_ts": "0.9"}
    print(f"[{'PASS' if sm_ok else 'FAIL'}] slackcode._clean_summary_message -> {sm}")
    if not sm_ok:
        fail += 1

    ar = _clean_agent_resource({"url": "https://figma.com/x", "resource_type": "design",
                                "title": "Mockups", "provider": "Figma", "junk": "drop"})
    ar_ok = ar == {"url": "https://figma.com/x", "resource_type": "design",
                   "title": "Mockups", "provider": "Figma"}
    print(f"[{'PASS' if ar_ok else 'FAIL'}] slackcode._clean_agent_resource -> {sorted(ar)}")
    if not ar_ok:
        fail += 1

    # _views_from tolerates envelope key naming; [] on a non-ok result.
    vf_ok = (
        _views_from(_SCR(ok=True, data={"views": [{"view_key": "a"}, "bad", {"view_key": "b"}]})) ==
        [{"view_key": "a"}, {"view_key": "b"}]
        and _views_from(_SCR(ok=True, data={"agent_session_views": [{"view_key": "c"}]})) == [{"view_key": "c"}]
        and _views_from(_SCR(ok=False, error="feature_disabled")) == []
    )
    print(f"[{'PASS' if vf_ok else 'FAIL'}] slackcode._views_from/tolerant-envelope")
    if not vf_ok:
        fail += 1

    # Canvas view flow (CONFIRMED live via the in-channel probe): canvases.create
    # → setView type=canvas by canvas_id. A canvas view attaches an existing
    # canvas by id; it does NOT take inline content (every inline variant returned
    # missing_required_arg because canvas_id was the missing arg).
    import asyncio as _asyncio_cv

    from agent.slackcode import (
        create_canvas,
        ensure_view_capacity,
        publish_canvas_view,
        set_canvas_content,
        set_view,
    )

    class _CaptureCall:
        """Captures every api_call(method, json=...) so we can assert payloads.
        Returns canned data per method so the two-step canvas flow completes."""

        def __init__(self, list_views_data=None):
            self.method = None
            self.json = None
            self.calls = []  # (method, json) in order
            self._lv = list_views_data

        async def api_call(self, method, *, json=None, **k):
            self.method = method
            self.json = json
            self.calls.append((method, json))
            if method == "agents.conversations.listViews":
                return {"ok": True, "views": self._lv or []}
            if method == "canvases.create":
                return {"ok": True, "canvas_id": "F0CANVAS1"}
            return {"ok": True}

    # set_view for canvas sends canvas_id (+ name/access_level), NOT content/document_content.
    capc = _CaptureCall()
    _asyncio_cv.run(set_view(capc, "C0", view_type="canvas", view_key="recap",
                             name="Recap", canvas_id="F0CANVAS1", access_level="comment"))
    canvas_shape_ok = (
        capc.method == "agents.conversations.setView"
        and capc.json.get("type") == "canvas"
        and capc.json.get("canvas_id") == "F0CANVAS1"
        and "content" not in capc.json and "document_content" not in capc.json
        and capc.json.get("access_level") == "comment"
        and capc.json.get("view_key") == "recap"
    )
    print(f"[{'PASS' if canvas_shape_ok else 'FAIL'}] slackcode.set_view/canvas-attaches-by-canvas_id")
    if not canvas_shape_ok:
        fail += 1

    # create_canvas → canvases.create with the structured document_content object.
    capcc = _CaptureCall()
    r_cc = _asyncio_cv.run(create_canvas(capcc, title="Recap", markdown="## Hi\nbody"))
    cc_ok = (
        capcc.method == "canvases.create"
        and capcc.json.get("title") == "Recap"
        and capcc.json.get("document_content") == {"type": "markdown", "markdown": "## Hi\nbody"}
        and r_cc.ok and (r_cc.data or {}).get("canvas_id") == "F0CANVAS1"
    )
    print(f"[{'PASS' if cc_ok else 'FAIL'}] slackcode.create_canvas/canvases.create-object")
    if not cc_ok:
        fail += 1

    # publish_canvas_view does the two-step in order: create THEN attach by id.
    cappv = _CaptureCall()
    r_pv = _asyncio_cv.run(publish_canvas_view(cappv, "C0", title="Recap", markdown="## Hi",
                                               view_key="recap", name="Recap", access_level="comment"))
    methods = [m for m, _ in cappv.calls]
    pv_ok = (
        methods == ["canvases.create", "agents.conversations.setView"]
        and cappv.calls[1][1].get("canvas_id") == "F0CANVAS1"   # attached the created id
        and cappv.calls[1][1].get("type") == "canvas"
        and r_pv.ok
    )
    print(f"[{'PASS' if pv_ok else 'FAIL'}] slackcode.publish_canvas_view/two-step-create-then-attach")
    if not pv_ok:
        fail += 1

    # html/diff still send flat `content`, never canvas_id/document_content.
    caph = _CaptureCall()
    _asyncio_cv.run(set_view(caph, "C0", view_type="html", view_key="preview",
                             name="Preview", content="<!doctype html><body>x</body>"))
    html_shape_ok = (
        caph.json.get("content", "").startswith("<!doctype")
        and "canvas_id" not in caph.json and "document_content" not in caph.json
    )
    print(f"[{'PASS' if html_shape_ok else 'FAIL'}] slackcode.set_view/html-stays-flat-content")
    if not html_shape_ok:
        fail += 1

    # set_canvas_content edits in place via canvases.edit (replace op), by canvas_id.
    capsc = _CaptureCall()
    _asyncio_cv.run(set_canvas_content(capsc, canvas_id="F0CANVAS1", markdown="edited"))
    scc_ok = (
        capsc.method == "canvases.edit"
        and capsc.json.get("canvas_id") == "F0CANVAS1"
        and capsc.json.get("changes") == [{"operation": "replace",
                                           "document_content": {"type": "markdown", "markdown": "edited"}}]
    )
    print(f"[{'PASS' if scc_ok else 'FAIL'}] slackcode.set_canvas_content/canvases.edit-replace")
    if not scc_ok:
        fail += 1

    # recap-canvas id registry: store → get → clear.
    from agent.slackcode import (
        clear_recap_canvas_id,
        get_recap_canvas_id,
        set_recap_canvas_id,
    )

    rch = "C0RECAP"
    clear_recap_canvas_id(rch)
    rc0 = get_recap_canvas_id(rch)
    set_recap_canvas_id(rch, "F0CANVAS1")
    rc1 = get_recap_canvas_id(rch)
    clear_recap_canvas_id(rch)
    rc2 = get_recap_canvas_id(rch)
    recap_reg_ok = rc0 is None and rc1 == "F0CANVAS1" and rc2 is None
    print(f"[{'PASS' if recap_reg_ok else 'FAIL'}] slackcode.recap-canvas-id registry")
    if not recap_reg_ok:
        fail += 1

    # ensure_view_capacity: no-op when the key already exists or under cap; prunes
    # the oldest keyed non-diff tab when at cap and adding a NEW key.
    existing = [{"view_key": "preview", "type": "html", "view_id": "v1", "date_added": 10},
                {"view_key": "dashboard", "type": "block_kit", "view_id": "v2", "date_added": 20}]
    cap_noop = _CaptureCall(list_views_data=existing)
    r_noop = _asyncio_cv.run(ensure_view_capacity(cap_noop, "C0", view_key="preview"))  # already present
    at_cap = [
        {"view_key": None, "type": "diff", "view_id": "d0", "date_added": 1},            # diff, never pruned
        {"view_key": "preview", "type": "html", "view_id": "v1", "date_added": 30},
        {"view_key": "dashboard", "type": "block_kit", "view_id": "v2", "date_added": 20},
        {"view_key": "old", "type": "html", "view_id": "v3", "date_added": 5},           # oldest keyed non-diff
        {"view_key": "canvasx", "type": "canvas", "view_id": "v4", "date_added": 40},
    ]
    cap_prune = _CaptureCall(list_views_data=at_cap)
    r_prune = _asyncio_cv.run(ensure_view_capacity(cap_prune, "C0", view_key="brand-new"))
    cap_ok2 = (
        r_noop.ok and r_noop.data.get("pruned") is None                    # under/at-key: no prune
        and cap_prune.method == "agents.conversations.removeView"          # at cap + new key: pruned
        and cap_prune.json.get("view_key") == "old"                        # the OLDEST keyed non-diff
        and len(at_cap) == MAX_VIEWS
        and r_prune.ok
    )
    print(f"[{'PASS' if cap_ok2 else 'FAIL'}] slackcode.ensure_view_capacity/prunes-oldest-nondiff-at-cap")
    if not cap_ok2:
        fail += 1

    # render_plan_as_text: title + check-marked steps, no streaming
    txt = render_plan_as_text({"title": "Thinking", "steps": [
        {"id": "1", "title": "Read the threads", "details": "18 msgs"},
        {"id": "2", "title": "Drafted the summary"},
    ]})
    rp_ok = "*Thinking*" in txt and ":white_check_mark: Read the threads" in txt and "18 msgs" in txt
    print(f"[{'PASS' if rp_ok else 'FAIL'}] slackcode.render_plan_as_text")
    if not rp_ok:
        fail += 1

    # replay-dedup guard: first claim wins, redelivery is skipped, clear re-arms it
    from agent.slackcode import (
        clear_session_started,
        mark_session_started,
    )

    key = "ses_C0TEST_1788926041121269"
    clear_session_started(key)  # ensure clean state if a prior run left it set
    first = mark_session_started(key)      # first mention → proceed
    replay = mark_session_started(key)     # Slack redelivery → skip
    empty = mark_session_started("")       # empty key never claims
    clear_session_started(key)
    rearmed = mark_session_started(key)    # after clear, a genuine retry proceeds
    clear_session_started(key)
    dedup_ok = first is True and replay is False and empty is False and rearmed is True
    print(f"[{'PASS' if dedup_ok else 'FAIL'}] slackcode.mark_session_started dedup")
    if not dedup_ok:
        fail += 1

    # Context-echo recognition: the cascade guard. When Slack creates a code
    # channel it auto-posts a "Context" backlink that quotes the origin (which
    # @-mentions the bot), re-firing as a message + app_mention (bot_id=None).
    # is_context_echo spots it from the event PAYLOAD markers (agent_channel_unfurl
    # attachment / agent_channel_origin_* blocks) — no API call, no channels:read,
    # no race — so the bot drops it instead of "answering" it or spawning a nested
    # channel. Fixtures use the exact payload shapes captured from a live run.
    from agent.slackcode import is_context_echo

    echo_fixtures = [
        # (label, event, expected_is_echo)
        ("real coding mention", {
            "text": "<@U0BOTEXAMPLE> migrate the billing cron to the new scheduler",
            "blocks": [{"type": "rich_text", "block_id": "YHIS+"}],
        }, False),
        # Live-log echo: carries the code-channel unfurl attachment.
        ("echo via agent_channel_unfurl", {
            "text": "<https://x.slack.com/archives/C0/p1|Context> from <#C0>:\n> <@U0BOTEXAMPLE> migrate the billing cron",
            "attachments": [{"agent_channel_unfurl": {"channel_id": "C0NEW", "joinable_from_channel_id": "C0ORIG"}}],
        }, True),
        # Live-log echo: carries an agent_channel_origin_context block.
        ("echo via origin_context block", {
            "text": "<https://x.slack.com/archives/C0/p1|Context> from <#C0>:\n> <@U0BOTEXAMPLE> migrate the billing cron",
            "blocks": [
                {"type": "context", "block_id": "agent_channel_origin_context"},
                {"type": "rich_text", "block_id": "YHIS+"},
            ],
        }, True),
        ("echo via origin_status block", {
            "text": "Started a session with <@U0BOTEXAMPLE> in <#C0NEW>",
            "blocks": [{"type": "context", "block_id": "agent_channel_origin_status_C0NEW"}],
        }, True),
        # A bare mention is NOT an echo — app_mentioned answers it with a prompt.
        ("bare mention, no markers", {"text": "<@U0BOTEXAMPLE>"}, False),
        ("empty event", {}, False),
        # Real asks must NOT be dropped (no echo markers present):
        ("channel-ref ask", {"text": "<@U0BOTEXAMPLE> summarize <#C0C0K1732N5|billing>"}, False),
        ("PR-link ask, no prose", {"text": "<@U0BOTEXAMPLE> <https://github.com/acme/pr/9|this PR>"}, False),
    ]
    for label, evt, expected in echo_fixtures:
        got = is_context_echo(evt)
        ok = got == expected
        status = "PASS" if ok else "FAIL"
        if status == "FAIL":
            fail += 1
        print(f"[{status}] slackcode.is_context_echo/{label} -> {got}")

    # ---- Scenario artifacts (the Artifacts golden-standard build) ----------
    # Every artifact's content comes from ONE scenario fact module so the diff,
    # preview, dashboard, and recap can never disagree. Each scenario declares
    # which tabs it publishes via ARTIFACTS; the orchestrator honors it. These
    # fixtures check the content shapes offline (no Slack).
    from scenarios import _KNOWN, is_seeded, load_scenario

    _KNOWN_ARTIFACTS = {"diff", "preview", "dashboard"}

    # Default + fallback behavior.
    # The live-launch path is: no SLACK_CODE_SCENARIO env → load_slack_code_config()
    # → cfg.scenario → load_scenario(cfg.scenario). A hardcoded default in the
    # config that disagreed with the scenarios package _DEFAULT once silently ran
    # the wrong scenario (billing instead of website → no Preview). Assert the
    # WHOLE path resolves to the intended default, not just load_scenario(None).
    import os as _os

    from agent.slackcode import load_slack_code_config

    _saved = _os.environ.pop("SLACK_CODE_SCENARIO", None)
    try:
        cfg_default = load_slack_code_config()
        live_default = load_scenario(cfg_default.scenario).SLUG
    finally:
        if _saved is not None:
            _os.environ["SLACK_CODE_SCENARIO"] = _saved
    default_checks = [
        ("load_scenario(None) is website_redesign", load_scenario(None).SLUG == "website_redesign"),
        ("unknown slug falls back to default",
         load_scenario("does-not-exist").SLUG == "website_redesign"),
        ("LIVE default (env unset → config → scenario) is website_redesign",
         live_default == "website_redesign"),
    ]
    for label, cond in default_checks:
        status = "PASS" if cond else "FAIL"
        if not cond:
            fail += 1
        print(f"[{status}] scenario/{label}")

    # Contract checks that apply to EVERY registered scenario.
    for slug in sorted(_KNOWN):
        sc = load_scenario(slug)
        arts = getattr(sc, "ARTIFACTS", None)

        # A SEEDLESS scenario (freeform, SEED = False) has NO scripted content —
        # the artifact is generated live and diffed per turn by the engine. It
        # only has to satisfy the seed contract (SLUG, ARTIFACTS, seed_filename),
        # so validate that and skip the scripted-content assertions below.
        if not is_seeded(sc):
            # A seedless scenario has no origin cast — PARTICIPANTS (if defined at
            # all) must be empty and there must be no phase beats.
            _seedless_parts = getattr(sc, "PARTICIPANTS", []) or []
            _seedless_beats = sc.participant_beats() if callable(getattr(sc, "participant_beats", None)) else []
            seedless = [
                ("SLUG matches", sc.SLUG == slug),
                ("ARTIFACTS is a valid subset",
                 isinstance(arts, set) and arts and arts.issubset(_KNOWN_ARTIFACTS) and "diff" in arts),
                ("seed_filename() returns a str", isinstance(sc.seed_filename(), str)),
                ("seedless has no scripted base_diff", not hasattr(sc, "base_diff")),
                ("seedless has no participants", not _seedless_parts and not _seedless_beats),
            ]
            for label, cond in seedless:
                status = "PASS" if cond else "FAIL"
                if not cond:
                    fail += 1
                print(f"[{status}] scenario.{slug}/{label}")
            continue

        common = [
            ("SLUG matches", sc.SLUG == slug),
            ("ARTIFACTS is a valid subset",
             isinstance(arts, set) and arts and arts.issubset(_KNOWN_ARTIFACTS) and "diff" in arts),
            ("base_diff is a diff", sc.base_diff().startswith("diff --git") and len(sc.base_diff()) > 80),
            ("patch_diff differs from base", sc.patch_diff() != sc.base_diff()),
            ("fail report red + offers patch",
             ":red_circle:" in sc.check_fail_report() and "patch" in sc.check_fail_report().lower()),
            ("pass report green",
             ":large_green_circle:" in sc.check_pass_report()),
            ("no double-asterisk bold in reports",
             "**" not in sc.check_fail_report() and "**" not in sc.check_pass_report()),
            ("recap has exactly 5 sections", sc.recap_canvas().count("## ") == 5),
            ("recap no double-asterisk bold", "**" not in sc.recap_canvas()),
            ("provenance non-empty dict", isinstance(sc.provenance(), dict) and len(sc.provenance()) >= 2),
            ("render_provenance non-empty", len(sc.render_provenance()) > 40),
            # Thinking pulse: a non-empty, single-asterisk line for every scripted action.
            ("thinking_line for all actions", all(
                isinstance(sc.thinking_line(a), str) and sc.thinking_line(a).strip()
                and "**" not in sc.thinking_line(a)
                for a in ("check", "patch", "metrics", "recap"))),
        ]
        # Human participants: 1–2 people (name + email_stem), and every beat names
        # one of them with a valid phase. This is what makes "humans + Claude" show.
        _parts = getattr(sc, "PARTICIPANTS", None) or []
        _pnames = {p.get("name") for p in _parts if isinstance(p, dict)}
        _beats = sc.participant_beats() if callable(getattr(sc, "participant_beats", None)) else []
        common += [
            ("PARTICIPANTS is 1–2 people", 1 <= len(_parts) <= 2),
            ("each participant has name + email_stem",
             all(isinstance(p, dict) and p.get("name") and p.get("email_stem") for p in _parts)),
            ("participant_beats non-empty", isinstance(_beats, list) and len(_beats) >= 1),
            ("every beat: valid phase, non-empty text, name ∈ PARTICIPANTS",
             all(isinstance(b, dict) and b.get("phase") in ("arrival", "on_artifacts")
                 and b.get("text") and b.get("name") in _pnames for b in _beats)),
            ("has an arrival beat (the stall)", any(b.get("phase") == "arrival" for b in _beats)),
            ("beats have no double-asterisk bold",
             all("**" not in (b.get("text") or "") for b in _beats)),
        ]
        # Dashboard is required only for scenarios that publish it.
        if arts and "dashboard" in arts:
            common.append(("dashboard blocks valid", validate_blocks(sc.dashboard_blocks()) == []))
        # Preview is required (and must be self-contained HTML) only if published.
        # A scenario that publishes a preview is a live-editable HTML artifact, so
        # it must also expose seed_filename() (the engine seeds turn-0 with it).
        if arts and "preview" in arts:
            html0 = sc.preview_html(False)
            html1 = sc.preview_html(True)
            common.append((
                "preview(False) is self-contained html",
                html0.lstrip().lower().startswith("<!doctype")
                and "http://" not in html0 and "https://" not in html0
                and "<script" not in html0.lower(),
            ))
            common.append(("preview changes when patched", html0 != html1))
            common.append((
                "seed_filename() returns a non-empty str (previewable scenario)",
                isinstance(sc.seed_filename(), str) and len(sc.seed_filename()) > 0,
            ))
        for label, cond in common:
            status = "PASS" if cond else "FAIL"
            if not cond:
                fail += 1
            print(f"[{status}] scenario.{slug}/{label}")

    # website_redesign specifics: the contrast fail→fix is the hero beat.
    web = load_scenario("website_redesign")
    web_checks = [
        ("preview ships white-on-amber CTA (the flaw)", "#FFFFFF" in web.preview_html(False)),
        ("patched preview darkens the CTA (the fix)", "#1A1A1A" in web.preview_html(True)),
        ("fail report names the contrast ratio", "1.9:1" in web.check_fail_report()),
        ("pass report names the fixed ratio", "10.8:1" in web.check_pass_report()),
        ("check label is contrast", web.CHECK_LABEL == "contrast"),
        ("publishes preview", "preview" in web.ARTIFACTS),
    ]
    for label, cond in web_checks:
        status = "PASS" if cond else "FAIL"
        if not cond:
            fail += 1
        print(f"[{status}] scenario.website_redesign/{label}")

    # billing_webhook specifics: no preview (it was a meta-diagram; removed).
    billing = load_scenario("billing_webhook")
    billing_checks = [
        ("billing has NO preview artifact", "preview" not in billing.ARTIFACTS),
        ("billing has no preview_html attr", not hasattr(billing, "preview_html")),
        ("billing publishes dashboard", "dashboard" in billing.ARTIFACTS),
        ("patch_diff is the durable fix", "wait_durable" in billing.patch_diff()),
    ]
    for label, cond in billing_checks:
        status = "PASS" if cond else "FAIL"
        if not cond:
            fail += 1
        print(f"[{status}] scenario.billing_webhook/{label}")

    # ---- Code-action recognizer (mention-keyword trigger for the demo beats) --
    from listeners.events.slack_code import recognize_code_action

    action_fixtures = [
        # (label, text, expected_action)
        ("run a check", "run a quick check right here", "check"),
        ("durability check", "can you run the durability check?", "check"),
        ("generate a patched version", "generate a patched version with the fixes", "patch"),
        ("fix it", "fix it right here please", "patch"),
        ("patch beats check", "apply the fix and re-run the check", "patch"),
        ("make a recap", "put together 5 slides for leadership", "recap"),
        ("deck", "turn this into a deck", "recap"),
        ("dashboard", "show me the dashboard", "metrics"),
        ("is it working", "is it actually working?", "metrics"),
        ("normal question", "why did you choose the durable queue?", None),
        ("plain chat", "thanks, this looks great", None),
        # New-scenario check phrasings must map to "check" (keep in step with the
        # scenarios' CHECK_LABELs: load-test / flake-stability / migration-safety).
        ("load test (checkout)", "run a load test", "check"),
        ("under load (checkout)", "does it hold up under load?", "check"),
        ("flake stability", "check the flake stability", "check"),
        ("is it still flaky", "is it still flaky?", "check"),
        ("migration safety", "is the migration safe to ship?", "check"),
        ("will it lock", "will it lock the table?", "check"),
        ("postmortem → recap", "write the postmortem", "recap"),
    ]
    for label, text, expected in action_fixtures:
        got = recognize_code_action(text)
        ok = got == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            fail += 1
        print(f"[{status}] slack_code.recognize_code_action/{label} -> {got}")

    # ---- FAQ-edit recognizer (the pre-baked marketer "Jennifer edit") --------
    # NEAR-EXACT matcher (not loose substring): only the scripted ask serves the
    # canned diff; a stray "faq" or a request for a DIFFERENT faq must fall through
    # to the live edit path. Also assert the FAQ phrase is NOT a recognize_code_action
    # keyword (no collision → routing order in the handlers is unambiguous).
    from listeners.events.slack_code import recognize_faq_edit

    faq_fixtures = [
        # (label, text, expected)
        ("canonical", "add an expandable FAQ section to the page", True),
        ("trailing period + case", "Add an expandable FAQ section to the page.", True),
        ("extra whitespace", "add an  expandable   faq   section", True),
        ("accordion variant", "add an expandable FAQ accordion to the page", True),
        ("bare faq", "faq", False),
        ("faq with JS", "can you add a FAQ maybe with some JS?", False),
        ("different faq", "add an expandable FAQ with a nice animation", False),
        ("unrelated edit", "add a contact form", False),
        ("empty", "", False),
    ]
    for label, text, expected in faq_fixtures:
        got = recognize_faq_edit(text)
        ok = got == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            fail += 1
        print(f"[{status}] slack_code.recognize_faq_edit/{label} -> {got}")
    # No collision with the code-action recognizer.
    collision_ok = recognize_code_action("add an expandable FAQ section to the page") is None
    if not collision_ok:
        fail += 1
    print(f"[{'PASS' if collision_ok else 'FAIL'}] slack_code.recognize_faq_edit/no-code-action-collision")

    # ---- website_redesign FAQ artifacts (the pre-baked edit's data) ----------
    from scenarios import load_scenario
    wr = load_scenario("website_redesign")
    _fdiff = wr.faq_diff()
    _pfaq = wr.preview_html(False, faq=True)
    _pplain = wr.preview_html(False)
    wr_checks = [
        ("faq_diff is a unified diff", isinstance(_fdiff, str) and _fdiff.startswith("diff --git")),
        ("faq_diff has accordion markup", "<details>" in _fdiff and "<summary>" in _fdiff),
        ("preview(faq=True) has accordion", "<details>" in _pfaq and "<summary>" in _pfaq),
        ("preview(faq=True) is JS-free", "<script" not in _pfaq.lower()),
        ("preview(faq=False) has NO accordion", "<details>" not in _pplain),
        ("preview(True) still accepts positional bool", wr._CTA_TEXT_GOOD in wr.preview_html(True)),
        ("preview(True, faq=True) composes", wr._CTA_TEXT_GOOD in wr.preview_html(True, faq=True)
         and "<details>" in wr.preview_html(True, faq=True)),
        ("thinking_line('open') is non-generic",
         bool(wr.thinking_line("open")) and wr.thinking_line("open") != "Working on it…"),
    ]
    for label, cond in wr_checks:
        status = "PASS" if cond else "FAIL"
        if not cond:
            fail += 1
        print(f"[{status}] scenario.website_redesign/{label}")

    # ---- Every scenario defines the _THINKING "open" key (session-start line) --
    # The DEFAULT story was missing it (drift); assert all seeded scenarios have a
    # non-generic "open" line so no scenario silently degrades on session start.
    for slug in ("website_redesign", "billing_webhook", "checkout_incident",
                 "flaky_test", "sql_migration"):
        sc = load_scenario(slug)
        line = sc.thinking_line("open") if hasattr(sc, "thinking_line") else ""
        cond = bool(line) and line != "Working on it…"
        status = "PASS" if cond else "FAIL"
        if not cond:
            fail += 1
        print(f"[{status}] scenario.{slug}/thinking_line('open')")

    # ---- Claude Tag: in-thread lightweight checklist -------------------------
    # (1) Routing split — Tag claims its curated phrases; a real build / code task does
    # not; and the canonical Code phrases still route Code (the top coupling risk).
    from scenarios_tag import route_tag_scenario, load_tag_scenario, _KNOWN as _TAG_KNOWN
    from agent import tagsession as _ts
    from agent.slackcode import SlackCodeConfig as _TagCfg, is_coding_task as _is_coding
    from datetime import datetime as _dt
    import logging as _tag_logging
    _tag_log = _tag_logging.getLogger("qa-tag")
    _tag_cfg_on = _TagCfg(enabled=True)

    tag_route_fixtures = [
        ("set up scheduled exports of the audit log", "scheduled_exports"),
        ("can you schedule exports nightly", "scheduled_exports"),
        ("update the blog, add a line under What's in the beta", "blog_update"),
        ("build me a snake game", ""),          # a real build → NOT Tag
        ("refactor the billing webhook", ""),   # a code task → NOT Tag
        ("what's the latest on the launch?", ""),  # a plain question → NOT Tag
    ]
    for text, want in tag_route_fixtures:
        got = route_tag_scenario(text)
        ok = got == want
        if not ok:
            fail += 1
        print(f"[{'PASS' if ok else 'FAIL'}] scenarios_tag.route_tag_scenario/{text[:32]!r} -> {got!r} (want {want!r})")
    # Code phrases must still route Code (is_coding_task True) and NOT be Tag.
    for text in ("refactor the billing webhook onto a durable queue", "roll back the payment-service deploy"):
        code_ok = _is_coding(text, _tag_cfg_on) and route_tag_scenario(text) == ""
        if not code_ok:
            fail += 1
        print(f"[{'PASS' if code_ok else 'FAIL'}] tag/code-split-still-routes-code/{text[:28]!r}")

    # (2) Tag scenario contract.
    for slug in _TAG_KNOWN:
        m = load_tag_scenario(slug)
        p = m.initial_plan()
        step_ids = {s["id"] for s in p.get("steps", [])}
        inserted = {d["insert"]["id"] for d in m.turn_deltas() if "insert" in d}
        checks = [
            ("SLUG matches module", m.SLUG == slug),
            ("KEYWORDS non-empty tuple", isinstance(m.KEYWORDS, tuple) and bool(m.KEYWORDS)),
            ("COMMITMENT str", isinstance(m.COMMITMENT, str) and bool(m.COMMITMENT)),
            ("initial_plan validates", validate_plan(p) == []),
            ("deltas reference known ids", all(
                sid in step_ids or sid in inserted
                for d in m.turn_deltas() for sid in (d.get("set") or {})
            )),
            ("result_line str|None", m.result_line() is None or isinstance(m.result_line(), str)),
            ("OPEN_IN_CLAUDE_URL str", isinstance(m.OPEN_IN_CLAUDE_URL, str)),
        ]
        for label, cond in checks:
            if not cond:
                fail += 1
            print(f"[{'PASS' if cond else 'FAIL'}] scenarios_tag.{slug}/{label}")

    # (3) Checklist render + advance + delta (pure helpers).
    _m = load_tag_scenario("scheduled_exports")
    _plan0 = _ts.start_plan(_m.initial_plan())
    _body = _ts.render_checklist_text(_plan0, now=_dt(2026, 9, 18, 15, 51))
    render_ok = (
        "✱ " in _body and "○ " in _body            # in-progress + pending glyphs
        and "_todos as of 3:51 PM_" in _body        # timestamped footer
        and _plan0["steps"][0]["status"] == "in_progress"
    )
    if not render_ok:
        fail += 1
    print(f"[{'PASS' if render_ok else 'FAIL'}] tagsession.render_checklist_text/glyphs+footer")
    # advance walks to all-complete
    _p, _done, _ticks = _plan0, False, 0
    while not _done and _ticks < 20:
        _p, _done = _ts.advance_plan(_p); _ticks += 1
    advance_ok = _done and all(s["status"] == "complete" for s in _p["steps"])
    if not advance_ok:
        fail += 1
    print(f"[{'PASS' if advance_ok else 'FAIL'}] tagsession.advance_plan/reaches-all-complete ({_ticks} ticks)")
    # delta inserts the replan step right after its anchor
    _pd = _ts.apply_delta(_ts.start_plan(_m.initial_plan()), _m.turn_deltas()[0])
    _ids = [s["id"] for s in _pd["steps"]]
    delta_ok = "replan" in _ids and _ids.index("replan") == _ids.index("scope") + 1
    if not delta_ok:
        fail += 1
    print(f"[{'PASS' if delta_ok else 'FAIL'}] tagsession.apply_delta/inserts-replan ({_ids})")

    # (4) Payload builders.
    _pp = _ts.post_payload("C1", "100.0", _plan0, open_in_claude_url=_m.OPEN_IN_CLAUDE_URL)
    _up = _ts.update_payload("C1", "999.0", _plan0)
    payload_ok = (
        _pp.get("thread_ts") == "100.0" and _pp.get("text") == "todos" and bool(_pp.get("blocks"))
        and _up.get("ts") == "999.0" and "channel" in _up and bool(_up.get("blocks"))
        and any(b.get("type") == "context" for b in _pp["blocks"])  # inert Open-in-Claude link
    )
    if not payload_ok:
        fail += 1
    print(f"[{'PASS' if payload_ok else 'FAIL'}] tagsession.payloads/post+update-shape")

    # (5) Capture-harness lifecycle: turn 0 = commitment + checklist post; auto-advance
    # = N chat.update on the SAME ts; NO set_view / startStream (Tag stays off the
    # code/stream paths). Runs with SLACK_TAG_STEP_DWELL_S=0 (no real sleep).
    import asyncio as _aio, os as _os
    from listeners.events import slack_tag as _tag
    _os.environ["SLACK_TAG_STEP_DWELL_S"] = "0"

    class _TagCapClient:
        def __init__(self):
            self.calls = []; self._n = 5000
        async def chat_postMessage(self, **k):
            self.calls.append(("postMessage", k)); self._n += 1
            return {"ok": True, "ts": f"{self._n}.0"}
        async def chat_update(self, **k):
            self.calls.append(("update", k)); return {"ok": True}
        def __getattr__(self, name):
            async def _f(*a, **k):
                self.calls.append((name, k)); return {"ok": True, "ts": "1.1"}
            return _f

    async def _run_tag_capture():
        c = _TagCapClient()
        ok = await _tag.run_tag_session(client=c, logger=_tag_log, channel_id="CTAG",
                                        thread_ts="777.0", slug="scheduled_exports", user_id="U1")
        t = _tag._ADVANCE_TASKS.get(("CTAG", "777.0"))
        if t:
            await _aio.wait_for(t, timeout=5)
        return ok, c.calls

    _tok, _tcalls = _aio.run(_run_tag_capture())
    _posts = [k for (m, k) in _tcalls if m == "postMessage"]
    _updates = [k for (m, k) in _tcalls if m == "update"]
    _upd_ts = {u.get("ts") for u in _updates}
    _no_code_paths = not any(m in ("set_view", "chat_startStream", "chat_appendStream", "chat_stopStream")
                             for (m, k) in _tcalls)
    lifecycle_ok = (
        _tok
        and len(_posts) >= 3                       # commitment + checklist + finish line
        and len(_updates) >= 4                      # auto-advance ticks
        and len(_upd_ts) == 1                        # all updates hit the SAME checklist ts (one writer)
        and any("#4131" in (p.get("markdown_text") or "") for p in _posts)  # scripted PR line
        and _no_code_paths
    )
    if not lifecycle_ok:
        fail += 1
    print(f"[{'PASS' if lifecycle_ok else 'FAIL'}] slack_tag.lifecycle/session→auto-advance→finish "
          f"(posts={len(_posts)}, updates={len(_updates)}, distinct_ts={len(_upd_ts)}, no_code_paths={_no_code_paths})")
    _ts.clear_tag_thread("CTAG", "777.0")

    # (6) Follow-up interrupt: a human correction cancels the tick task and inserts the
    # replan step — WITHOUT the bot spoofing any human chime-in (Claude only spoofs in a
    # code channel, never in a thread). So assert the replan landed AND no spoofed
    # username post was made (no post_as_participant / customize username).
    async def _run_tag_followup_capture():
        c = _TagCapClient()
        await _tag.run_tag_session(client=c, logger=_tag_log, channel_id="CTAG2",
                                   thread_ts="888.0", slug="scheduled_exports", user_id="U1")
        await _tag.run_tag_followup(client=c, logger=_tag_log, channel_id="CTAG2", thread_ts="888.0",
                                    text="hold up, should be workspace-level not per-user", user_id="U2")
        st = _ts.get_tag_thread("CTAG2", "888.0")
        t = _tag._ADVANCE_TASKS.get(("CTAG2", "888.0"))
        if t:
            await _aio.wait_for(t, timeout=5)
        return c.calls, st

    _fcalls, _fst = _aio.run(_run_tag_followup_capture())
    _fids = [s["id"] for s in (_fst or {}).get("plan", {}).get("steps", [])] if _fst else []
    _spoofed = any(k.get("username") for (m, k) in _fcalls)  # any post with a spoofed username
    followup_ok = "replan" in _fids and not _spoofed
    if not followup_ok:
        fail += 1
    print(f"[{'PASS' if followup_ok else 'FAIL'}] slack_tag.followup/interrupt+replan-no-spoof "
          f"(replan={'replan' in _fids}, spoofed_post={_spoofed})")
    _ts.clear_tag_thread("CTAG2", "888.0")

    # (7) RACE regression: a correction that arrives AFTER the checklist has already
    # finished must still land — re-open the thread, insert the replan, re-finish, and
    # post the closing PR line EXACTLY once (no double-post). This reproduces the live
    # bug where a correction at the completion boundary was silently dropped.
    async def _run_tag_race_capture():
        c = _TagCapClient()
        await _tag.run_tag_session(client=c, logger=_tag_log, channel_id="CTAG3",
                                   thread_ts="999.0", slug="scheduled_exports", user_id="U1")
        # Let the auto-advance run FULLY to completion first (done=True, PR line posted).
        t = _tag._ADVANCE_TASKS.get(("CTAG3", "999.0"))
        if t:
            await _aio.wait_for(t, timeout=5)
        st_done = _ts.get_tag_thread("CTAG3", "999.0")
        # Now a late correction arrives — should RE-OPEN and replan.
        await _tag.run_tag_followup(client=c, logger=_tag_log, channel_id="CTAG3", thread_ts="999.0",
                                    text="actually workspace-level not per-user", user_id="U2")
        t2 = _tag._ADVANCE_TASKS.get(("CTAG3", "999.0"))
        if t2:
            await _aio.wait_for(t2, timeout=5)
        st_final = _ts.get_tag_thread("CTAG3", "999.0")
        return c.calls, st_done, st_final

    _rcalls, _rdone, _rfinal = _aio.run(_run_tag_race_capture())
    _was_done = bool((_rdone or {}).get("done"))
    _rids = [s["id"] for s in (_rfinal or {}).get("plan", {}).get("steps", [])] if _rfinal else []
    # closing PR line ("#4131") posted exactly once despite finishing twice
    _pr_posts = sum(1 for (m, k) in _rcalls if m == "postMessage" and "#4131" in (k.get("markdown_text") or ""))
    race_ok = _was_done and ("replan" in _rids) and (_pr_posts == 1)
    if not race_ok:
        fail += 1
    print(f"[{'PASS' if race_ok else 'FAIL'}] slack_tag.followup/late-correction-reopens-and-replans "
          f"(was_done={_was_done}, replan={'replan' in _rids}, pr_posts={_pr_posts})")
    _ts.clear_tag_thread("CTAG3", "999.0")

    # (8) ROUTING: an @-mention that is a REPLY in an active Tag thread must route to
    # run_tag_followup (not a generic reply / new session). In a non-code channel the
    # correction MUST @-mention Claude to be delivered, so this is the only steer path
    # — the gate lives in handle_app_mentioned. We monkeypatch the module's config +
    # run_tag_followup, seed an active Tag thread, and drive the handler with a fake
    # mentioned reply, asserting the followup fires. Also assert a TOP-LEVEL mention
    # (thread_ts == its own ts) does NOT hit the followup.
    import listeners.events.app_mentioned as _am
    from agent.slackcode import SlackCodeConfig as _AmCfg

    _ts.set_tag_thread("CTAGM", "555.0", {"slug": "scheduled_exports", "checklist_ts": "556.0",
                                          "plan": load_tag_scenario("scheduled_exports").initial_plan(),
                                          "turn": 0, "done": False})

    class _Ctx:
        def __init__(self, ch, uid): self.channel_id = ch; self.user_id = uid; self.user_token = None; self.bot_user_id = "UBOT"

    async def _noop(*a, **k):
        return None

    _fu_calls = []

    async def _fake_followup(**k):
        _fu_calls.append(k)

    async def _fake_session(**k):
        return True  # pretend a new Tag session opened (top-level case), so no fall-through

    async def _run_am(event):
        _orig = (_am._SLACK_CODE_CFG, _am.run_tag_followup, _am.run_tag_session, _am.slackcode.is_code_channel)
        _am._SLACK_CODE_CFG = _AmCfg(enabled=True, tag_enabled=True)
        _am.run_tag_followup = _fake_followup
        _am.run_tag_session = _fake_session
        async def _not_code(*a, **k):
            return False
        _am.slackcode.is_code_channel = _not_code
        try:
            await _am.handle_app_mentioned(
                client=None, context=_Ctx(event["channel"], "U2"), event=event,
                logger=_tag_log, say=_noop, say_stream=_noop, set_status=_noop,
            )
        finally:
            _am._SLACK_CODE_CFG, _am.run_tag_followup, _am.run_tag_session, _am.slackcode.is_code_channel = _orig

    # A reply (thread_ts != ts) in the active Tag thread → followup fires.
    _aio.run(_run_am({"channel": "CTAGM", "ts": "560.0", "thread_ts": "555.0",
                      "text": "<@UBOT> actually workspace-level not per-user"}))
    reply_routed = len(_fu_calls) == 1 and _fu_calls[0].get("thread_ts") == "555.0"
    # A fresh TOP-LEVEL mention (thread_ts == ts) must NOT be treated as a followup.
    _fu_calls.clear()
    _aio.run(_run_am({"channel": "CTAGM", "ts": "600.0", "thread_ts": "600.0",
                      "text": "<@UBOT> set up scheduled exports"}))
    toplevel_not_followup = len(_fu_calls) == 0
    route_ok = reply_routed and toplevel_not_followup
    if not route_ok:
        fail += 1
    print(f"[{'PASS' if route_ok else 'FAIL'}] app_mentioned.tag-followup-routing "
          f"(reply_routed={reply_routed}, toplevel_skips={toplevel_not_followup})")
    _ts.clear_tag_thread("CTAGM", "555.0")

    # (9) SELF-FILTER: is_own_bot_event must skip only OUR bot's echo, NOT any bot_id.
    # An admin/xoxp-token HUMAN post carries the Slack Admin app's bot_id (a DIFFERENT
    # id) — blanket-dropping any bot_id silently discards real teammate corrections
    # (the documented admin-token trap). Assert: our own bot_id → True (skip);
    # a different bot_id → False (deliver); a plain human → False.
    from agent import slackcode as _sc
    _saved_ident = _sc._BOT_IDENTITY
    _sc._BOT_IDENTITY = {"team_id": "T", "user_id": "UBOT", "bot_id": "BSELF"}

    async def _own(ev):
        return await _sc.is_own_bot_event(object(), ev)

    _own_echo = _aio.run(_own({"bot_id": "BSELF", "user": "UBOT"}))
    _admin_human = _aio.run(_own({"bot_id": "BADMIN", "user": "U0HUMAN"}))
    _plain_human = _aio.run(_own({"user": "U0HUMAN"}))
    _sc._BOT_IDENTITY = _saved_ident
    selffilter_ok = (_own_echo is True) and (_admin_human is False) and (_plain_human is False)
    if not selffilter_ok:
        fail += 1
    print(f"[{'PASS' if selffilter_ok else 'FAIL'}] slackcode.is_own_bot_event/own-only "
          f"(own={_own_echo}, admin_token_human={_admin_human}, plain_human={_plain_human})")

    # ---- Patched-channel registry (the check→patch→re-check state) ----------
    from agent.slackcode import (
        clear_channel_patched,
        is_channel_patched,
        mark_channel_patched,
    )

    ch = "C0PATCHTEST"
    clear_channel_patched(ch)
    before = is_channel_patched(ch)          # fresh → not patched → check FAILS
    mark_channel_patched(ch)
    after = is_channel_patched(ch)           # patched → check PASSES
    clear_channel_patched(ch)
    reset = is_channel_patched(ch)           # fresh session clears it
    patched_ok = before is False and after is True and reset is False
    print(f"[{'PASS' if patched_ok else 'FAIL'}] slackcode.patched-channel registry")
    if not patched_ok:
        fail += 1

    # ---- Live-artifact: ```html``` extraction --------------------------------
    from agent.htmlblock import extract_html

    html_fixtures = [
        # (label, reply, expect_html_prefix_or_None, expect_prose_substr)
        ("strict leading fence",
         "```html\n<!doctype html>\n<title>Snake</title>\n```\nDone — in the Code tab.",
         "<!doctype html>", "Done"),
        ("embedded fence after prose",
         "Here you go:\n```html\n<html><body>hi</body></html>\n```\nEnjoy!",
         "<html>", "Enjoy!"),
        ("bare fence, html tag dropped",
         "```\n<!DOCTYPE html>\n<body>x</body>\n```\nok",
         "<!DOCTYPE html>", "ok"),
        ("no html → passthrough (plain answer)",
         "I used a 20x20 grid to balance challenge and screen size.",
         None, "20x20 grid"),
        ("non-html code fence ignored",
         "```python\nprint('hi')\n```\nthat is python",
         None, "python"),
    ]
    for label, reply, want_prefix, want_prose in html_fixtures:
        h, rest = extract_html(reply)
        if want_prefix is None:
            ok = h is None and want_prose in rest
        else:
            ok = h is not None and h.lstrip().startswith(want_prefix) and want_prose in rest
        status = "PASS" if ok else "FAIL"
        if not ok:
            fail += 1
        print(f"[{status}] htmlblock.extract_html/{label}")

    # ---- Live-artifact: unified diff (full first turn vs incremental) --------
    from agent.artifact_diff import unified as _unified

    d_full = _unified("", "<a>\n<b>\n<c>", "snake-game.html")
    d_incr = _unified("<a>\n<x>\n<c>", "<a>\n<y>\n<c>", "snake-game.html")
    d_same = _unified("<a>\n<b>", "<a>\n<b>", "x.html")
    diff_checks = [
        ("first turn has git header", d_full.startswith("diff --git a/snake-game.html b/snake-game.html\n")),
        ("first turn is all-additions hunk", "@@ -0,0 +1,3 @@" in d_full),
        ("first turn has 3 added content lines",
         sum(1 for ln in d_full.splitlines() if ln.startswith("+") and not ln.startswith("+++")) == 3),
        ("incremental shows the changed line only",
         "-<x>" in d_incr and "+<y>" in d_incr and " <a>" in d_incr),
        ("incremental keeps context", " <c>" in d_incr),
        ("no change → empty diff", d_same == ""),
    ]
    for label, cond in diff_checks:
        status = "PASS" if cond else "FAIL"
        if not cond:
            fail += 1
        print(f"[{status}] artifact_diff.unified/{label}")

    # ---- Live-artifact: per-channel artifact registry ------------------------
    from agent.slackcode import (
        clear_channel_artifact,
        get_channel_artifact,
        set_channel_artifact,
    )

    ach = "C0ARTIFACT"
    clear_channel_artifact(ach)
    a0 = get_channel_artifact(ach)                       # fresh → None
    set_channel_artifact(ach, filename="snake-game.html", html="<v1>")
    a1 = get_channel_artifact(ach)                       # stored v1
    set_channel_artifact(ach, filename="snake-game.html", html="<v2>")
    a2 = get_channel_artifact(ach)                       # replaced with v2
    clear_channel_artifact(ach)
    a3 = get_channel_artifact(ach)                       # cleared → None
    art_ok = (
        a0 is None
        and a1 == {"filename": "snake-game.html", "html": "<v1>"}
        and a2 == {"filename": "snake-game.html", "html": "<v2>"}
        and a3 is None
    )
    print(f"[{'PASS' if art_ok else 'FAIL'}] slackcode.channel-artifact registry")
    if not art_ok:
        fail += 1

    # ---- Live-artifact: filename derivation ----------------------------------
    from listeners.events.slack_code import filename_for

    name_fixtures = [
        ("create a simple html based snake game for me", "snake"),
        ("build me a pricing page", "pricing"),
        ("make a countdown timer", "countdown"),
    ]
    for text, want_sub in name_fixtures:
        got = filename_for(text)
        ok = got.endswith(".html") and want_sub in got
        status = "PASS" if ok else "FAIL"
        if not ok:
            fail += 1
        print(f"[{status}] slack_code.filename_for/{text[:24]!r} -> {got}")

    # ---- Live-artifact: run_artifact_turn aborts on a stopped session --------
    # A stop landing before the turn starts must make NO Slack calls (no setStatus,
    # no setView, no chat.*). We fake a client that records every call and assert
    # none happen when the channel is already marked stopped.
    import asyncio as _asyncio

    from agent.slackcode import (
        clear_session_stopped,
        load_slack_code_config,
        mark_session_stopped,
    )
    from listeners.events.slack_code import run_artifact_turn

    class _RecordingClient:
        def __init__(self):
            self.calls: list[str] = []

        def __getattr__(self, name):
            async def _rec(*a, **k):
                self.calls.append(name)
                return {"ok": True, "ts": "1.1"}
            return _rec

    stop_ch = "C0STOPPED"
    mark_session_stopped(stop_ch)
    rec = _RecordingClient()
    import logging as _logging
    _log = _logging.getLogger("qa-stoptest")
    _cfg = load_slack_code_config()
    res = _asyncio.run(run_artifact_turn(
        client=rec, logger=_log, cfg=_cfg, channel_id=stop_ch,
        task_text="make a snake game", first_turn=True, user_id="U0",
    ))
    clear_session_stopped(stop_ch)
    stop_ok = res.reply_ts is None and res.ok is False and rec.calls == []
    print(f"[{'PASS' if stop_ok else 'FAIL'}] slack_code.run_artifact_turn/aborts-when-stopped "
          f"(calls={rec.calls})")
    if not stop_ok:
        fail += 1

    # A stop landing DURING the seeded-display publish (which awaits) must not let
    # the subsequent setStatus(processing) fire — the gap a review caught. Simulate
    # by marking the channel stopped from inside the first awaited set_view call,
    # then assert no chat_appendStream/set_session_status happens afterward. We
    # approximate "an await that yields then a stop arrives" with a client whose
    # first api_call marks the session stopped.
    from agent.slackcode import clear_channel_artifact as _cca

    stop_ch2 = "C0STOPMIDPUBLISH"
    clear_session_stopped(stop_ch2)
    _cca(stop_ch2)

    class _StopOnFirstCallClient:
        """Records calls; the FIRST api_call marks the session stopped, mimicking
        agent_session_stopped arriving during the seeded publish's awaits."""

        def __init__(self):
            self.calls: list[str] = []

        def __getattr__(self, name):
            async def _rec(*a, **k):
                self.calls.append(name)
                if len(self.calls) == 1:
                    mark_session_stopped(stop_ch2)
                return {"ok": True, "ts": "1.1"}
            return _rec

    rec2 = _StopOnFirstCallClient()
    _asyncio.run(run_artifact_turn(
        client=rec2, logger=_log, cfg=_cfg, channel_id=stop_ch2,
        task_text="implement the homepage redesign", first_turn=True,
        seed_html="<!doctype html><body>seed</body>", seed_filename="index.html",
        branch="master", user_id="U0",
    ))
    clear_session_stopped(stop_ch2)
    _cca(stop_ch2)
    # The engine calls slackcode.set_session_status, which under the hood calls
    # client.api_call(...); our shim records WRAPPER attribute names. The stop is set
    # on call #1. The robust invariant on a mid-turn stop: the turn must NOT post the
    # reply (no chat_postMessage) and must NOT leave a stream open — if it opened the
    # Thinking spinner (chat_startStream) it must also have closed it (chat_stopStream).
    # (The Thinking stream now opens before the seed publish, so on this stop path it
    # may open+close cleanly; what must never happen is a posted reply or a stranded
    # spinner after the stop.)
    _opened_stream = "chat_startStream" in rec2.calls
    _closed_stream = "chat_stopStream" in rec2.calls
    midstop_ok = (
        "chat_postMessage" not in rec2.calls              # no reply posted after stop
        and (not _opened_stream or _closed_stream)         # any opened spinner was closed
    )
    print(f"[{'PASS' if midstop_ok else 'FAIL'}] slack_code.run_artifact_turn/no-writes-after-mid-publish-stop "
          f"(calls={rec2.calls})")
    if not midstop_ok:
        fail += 1

    # An agent that "succeeds" but returns an EMPTY reply (the observed 401 case:
    # the host CLI printed a 401 into the channel and handed back a blank string)
    # must NOT be reported as a built artifact. run_artifact_turn should return
    # artifact_present=False + ok=False so the session-start summary ("Built X,
    # it's in the Code tab") is suppressed. We monkeypatch run_agent_offloop to
    # return ("", None) and assert the result flags + that no file was stored.
    import listeners.events.slack_code as _sc_mod

    empty_ch = "C0EMPTYAGENT"
    clear_session_stopped(empty_ch)
    _cca(empty_ch)
    _orig_offloop = _sc_mod.run_agent_offloop

    async def _empty_agent(*a, **k):
        return "", None

    rec3 = _RecordingClient()
    try:
        _sc_mod.run_agent_offloop = _empty_agent
        res3 = _asyncio.run(_sc_mod.run_artifact_turn(
            client=rec3, logger=_log, cfg=_cfg, channel_id=empty_ch,
            task_text="create a simple html snake game", first_turn=True,
            branch="master", user_id="U0",
        ))
    finally:
        _sc_mod.run_agent_offloop = _orig_offloop
    stored = get_channel_artifact(empty_ch)
    _cca(empty_ch)
    empty_ok = (
        res3.artifact_present is False
        and res3.ok is False
        and res3.reply_ts is None
        and stored is None  # freeform, no seed → nothing published/stored on a blank reply
    )
    print(f"[{'PASS' if empty_ok else 'FAIL'}] slack_code.run_artifact_turn/empty-reply→no-artifact-no-summary "
          f"(artifact_present={res3.artifact_present}, ok={res3.ok})")
    if not empty_ok:
        fail += 1

    # ---- One-agent-many-stories: keyword routing --------------------------------
    from scenarios import route_scenario

    route_fixtures = [
        # (text, expected_slug)
        ("implement the homepage redesign", "website_redesign"),
        ("can you redesign the landing page hero", "website_redesign"),
        ("refactor the billing webhook to a durable queue", "billing_webhook"),
        ("fix the stripe webhook durability", "billing_webhook"),
        ("can you create a simple html based snake game for me?", "freeform"),
        ("build me a pricing page", "freeform"),
        ("make a countdown timer widget", "freeform"),
        # The three incident/eng stories — their seed openers must route right.
        ("roll back the payment-service deploy and fix the retry storm", "checkout_incident"),
        ("checkout API latency spiked, orders failing — can you help", "checkout_incident"),
        ("the login_redirect test is flaky, find the root cause and fix it for real", "flaky_test"),
        ("this test keeps failing intermittently, everyone just hits re-run", "flaky_test"),
        ("add a last_login_at column to users, backfill it, and expose it on /me", "sql_migration"),
        ("write the migration to add a column and backfill it safely", "sql_migration"),
    ]
    for text, want in route_fixtures:
        got = route_scenario(text)
        ok = got == want
        status = "PASS" if ok else "FAIL"
        if not ok:
            fail += 1
        print(f"[{status}] scenarios.route_scenario/{text[:34]!r} -> {got} (want {want})")

    # The opener for each seeded story must ALSO trip the coding-task intent gate,
    # or no code channel opens (route_scenario is only consulted after the gate
    # fires). Keep is_coding_task keywords in step with route_scenario.
    _gate_cfg = SlackCodeConfig(enabled=True)
    gate_openers = [
        "roll back the payment-service deploy and fix the retry storm",
        "the login_redirect test is flaky, find the root cause and fix it for real",
        "add a last_login_at column to users, backfill it, and expose it on /me",
    ]
    for text in gate_openers:
        gated = is_coding_task(text, _gate_cfg)
        status = "PASS" if gated else "FAIL"
        if not gated:
            fail += 1
        print(f"[{status}] slackcode.is_coding_task/story-opener {text[:40]!r} -> {gated}")
    # Unmatched must NOT be hijacked into a seeded story by a stale env default:
    # route_scenario ignores a freeform-unmatched text's default unless explicitly
    # a non-freeform known slug passed by a deliberate caller. The standard call
    # (default="") always yields freeform for an unmatched build task.
    default_guard_ok = (
        route_scenario("make a snake game", default="website_redesign") == "website_redesign"
        # ^ explicit non-freeform default IS honored when passed deliberately …
        and route_scenario("make a snake game") == "freeform"
        # … but the standard no-default call routes unmatched → freeform.
    )
    print(f"[{'PASS' if default_guard_ok else 'FAIL'}] scenarios.route_scenario/default-only-when-explicit")
    if not default_guard_ok:
        fail += 1

    # ---- Per-channel scenario registry (locks a channel to its story) -----------
    from agent.slackcode import (
        clear_channel_scenario,
        get_channel_scenario,
        set_channel_scenario,
    )

    sch = "C0SCENARIO"
    clear_channel_scenario(sch)
    s0 = get_channel_scenario(sch)                        # fresh → None (caller falls back to env)
    set_channel_scenario(sch, "billing_webhook")
    s1 = get_channel_scenario(sch)                        # locked to billing
    set_channel_scenario(sch, "freeform")
    s2 = get_channel_scenario(sch)                        # a re-set updates it
    clear_channel_scenario(sch)
    s3 = get_channel_scenario(sch)                        # fresh session clears it
    scen_reg_ok = s0 is None and s1 == "billing_webhook" and s2 == "freeform" and s3 is None
    print(f"[{'PASS' if scen_reg_ok else 'FAIL'}] slackcode.channel-scenario registry")
    if not scen_reg_ok:
        fail += 1

    # Durability: the per-channel registries must SURVIVE a bot restart, or a
    # follow-up check after a relaunch runs the DEFAULT scenario (the wrong-story
    # bug we hit live). Simulate a restart: write state, then re-read the on-disk
    # file with a fresh _load_state() and assert the scenario lock + patched flag
    # are still there. Uses a temp state path so the real file isn't touched.
    import os as _os_dur

    import agent.slackcode as _sc_dur

    _dur_ch = "C0DURABLE"
    _saved_path = _sc_dur._STATE_PATH
    _tmp_state = _os_dur.path.join(_os.environ.get("TMPDIR", "/tmp"), "qa_slack_code_state.json")
    try:
        _sc_dur._STATE_PATH = _tmp_state
        _os_dur.path.exists(_tmp_state) and _os_dur.remove(_tmp_state)
        _sc_dur.clear_channel_scenario(_dur_ch)   # writes state
        _sc_dur.set_channel_scenario(_dur_ch, "checkout_incident")  # writes state
        _sc_dur.mark_channel_patched(_dur_ch)     # writes state
        # Re-read the file as a fresh process would at import.
        reloaded = _sc_dur._load_state()
        dur_ok = (
            reloaded.get("scenario", {}).get(_dur_ch) == "checkout_incident"
            and _dur_ch in reloaded.get("patched", [])
        )
        _sc_dur.clear_channel_scenario(_dur_ch)
        _sc_dur.clear_channel_patched(_dur_ch)
    finally:
        _sc_dur._STATE_PATH = _saved_path
        try:
            _os_dur.remove(_tmp_state)
        except OSError:
            pass
    print(f"[{'PASS' if dur_ok else 'FAIL'}] slackcode.durable-state/survives-restart")
    if not dur_ok:
        fail += 1

    # _scenario_for resolves the channel's stored story, else the cfg default.
    from listeners.events.slack_code import _scenario_for

    clear_channel_scenario(sch)
    _cfg_sf = load_slack_code_config()
    _cfg_sf.scenario = "website_redesign"
    # no stored scenario → falls back to cfg default
    fallback_slug = _scenario_for(sch, _cfg_sf).SLUG
    set_channel_scenario(sch, "billing_webhook")
    locked_slug = _scenario_for(sch, _cfg_sf).SLUG   # stored story wins over the default
    clear_channel_scenario(sch)
    sf_ok = fallback_slug == "website_redesign" and locked_slug == "billing_webhook"
    print(f"[{'PASS' if sf_ok else 'FAIL'}] slack_code._scenario_for/stored-wins-else-default "
          f"(fallback={fallback_slug}, locked={locked_slug})")
    if not sf_ok:
        fail += 1

    # ---- Two-phase plan render (Thinking spinner while working) -----------------
    # Bug #1 fix: the plan's steps must show `in_progress` (spinner) in phase 1 —
    # BEFORE work runs — and only flip to `complete` in phase 2, AFTER it. A fake
    # streamer records the status of every task chunk it receives so we can assert
    # the phase ordering (no premature `complete`).
    from agent.render import render_plan_steps_complete, render_plan_steps_pending

    class _CapturingStreamer:
        def __init__(self):
            self.statuses: list[str] = []
            self.completed_ids: list[str] = []  # ids that received a `complete` chunk
            self.stopped = False
            self.ts = "1.1"

        async def append(self, *, chunks=None, markdown_text=None):
            for ch in chunks or []:
                st = getattr(ch, "status", None)
                if st is not None:
                    self.statuses.append(st)
                if st == "complete":
                    cid = getattr(ch, "id", None)
                    if cid is not None:
                        self.completed_ids.append(cid)

        async def stop(self, *, markdown_text=None, blocks=None):
            self.stopped = True

    _plan = {
        "mode": "plan", "title": "Thinking",
        "steps": [
            {"id": "a", "title": "Read the request"},
            {"id": "b", "title": "Wrote the file"},
            {"id": "c", "title": "Refreshed the tabs"},
        ],
    }
    _cap = _CapturingStreamer()
    _asyncio.run(render_plan_steps_pending(_cap, _plan))
    pending_statuses = list(_cap.statuses)
    _asyncio.run(render_plan_steps_complete(_cap, _plan))
    complete_statuses = _cap.statuses[len(pending_statuses):]
    twophase_ok = (
        # Phase 1: every step spinning, none complete yet.
        pending_statuses == ["in_progress", "in_progress", "in_progress"]
        # Phase 2: every step settles to complete.
        and complete_statuses == ["complete", "complete", "complete"]
    )
    print(f"[{'PASS' if twophase_ok else 'FAIL'}] render.two-phase/pending-spins-then-completes "
          f"(pending={pending_statuses}, complete={complete_statuses})")
    if not twophase_ok:
        fail += 1

    # Regression: finalize must complete the SAME plan it opened with (same ids),
    # NOT the agent's own taskplan. The observed bug — 3 spinners that never
    # resolved while the agent's plan rendered as fresh bullets below — was
    # finalize completing a DIFFERENT plan's ids, so the pending cards never got a
    # `complete` chunk. Here we open with _WORKING_PLAN, hand finalize an agent
    # reply carrying a DIFFERENT taskplan, and assert the completed ids are the
    # pending plan's ids (understand/build/publish), so every spinner settles.
    from listeners.events.slack_code import (
        _WORKING_PLAN,
        finalize_thinking_stream,
    )

    _cap2 = _CapturingStreamer()
    _asyncio.run(render_plan_steps_pending(_cap2, _WORKING_PLAN))
    _agent_reply_with_other_plan = (
        "```taskplan\n"
        '{"mode":"plan","title":"Thinking","steps":['
        '{"id":"g1","title":"Planned the game structure"},'
        '{"id":"g2","title":"Wrote the canvas logic"}]}'
        "\n```\nBuilt the snake game — take a look."
    )
    _asyncio.run(finalize_thinking_stream(
        streamer=_cap2, plan=_WORKING_PLAN, logger=_log,
        response_text=_agent_reply_with_other_plan,
    ))
    pending_ids = [s["id"] for s in _WORKING_PLAN["steps"]]
    same_plan_ok = (
        _cap2.completed_ids == pending_ids  # completed OUR ids, all of them
        and _cap2.stopped                    # stream was closed (indicator ends)
    )
    print(f"[{'PASS' if same_plan_ok else 'FAIL'}] slack_code.finalize_thinking_stream/completes-pending-ids-not-agents "
          f"(completed={_cap2.completed_ids}, want={pending_ids})")
    if not same_plan_ok:
        fail += 1

    # ---- Status ALWAYS returns to `active` on turn exit (Bug #2) -----------------
    # The "Stop agent" spinner is Slack's `processing` UI; it must clear on every
    # exit path. We record the setStatus values a turn issues and assert the LAST
    # one is `active`, both on the success path and when the agent RAISES. Uses a
    # client that captures agents.sessions.setStatus calls via api_call.
    class _StatusClient:
        """Captures setStatus values (via api_call) and no-ops everything else,
        including the streaming methods so the turn's finalize path runs. Also
        records chat.startStream / chat.stopStream so a test can assert the stream
        was actually closed (its loading indicator cleared) on a given exit path."""

        def __init__(self):
            self.statuses: list[str] = []
            self.stream_calls: list[str] = []  # "start" / "stop", in order
            self.appended_chunks = False       # did any append use chunks?
            self.stop_mode: str | None = None   # LAST stop's mode (back-compat)
            self.stop_modes: list[str] = []     # EVERY stop's mode, in order
            self.posted = 0                     # chat.postMessage count
            self.events: list[str] = []         # ordered trace: "setView", "agent", …

        async def api_call(self, method, *, json=None, **k):
            if method == "agents.sessions.setStatus" and json:
                self.statuses.append(json.get("status"))
            if method == "agents.conversations.setView":
                self.events.append("setView")
            return {"ok": True, "ts": "1.1"}

        async def chat_postMessage(self, *a, **k):
            self.posted += 1
            return {"ok": True, "ts": "1.1"}

        async def chat_startStream(self, *a, **k):
            self.stream_calls.append("start")
            self.events.append("stream_start")
            return {"ok": True, "ts": "1.1"}

        async def chat_appendStream(self, *a, **k):
            if k.get("chunks") is not None:
                self.appended_chunks = True
            return {"ok": True, "ts": "1.1"}

        async def chat_stopStream(self, *a, **k):
            self.stream_calls.append("stop")
            self.events.append("stream_stop")
            # Record the payload mode of the FINAL (real) close — the streaming_mode_
            # mismatch bug was a chunks-mode stream being stopped with markdown_text.
            if k.get("chunks") is not None:
                self.stop_mode = "chunks"
            elif k.get("blocks") is not None:
                self.stop_mode = "blocks"
            elif k.get("markdown_text") is not None:
                self.stop_mode = "markdown_text"
            else:
                self.stop_mode = "empty"
            self.stop_modes.append(self.stop_mode)
            return {"ok": True, "ts": "1.1"}

        def __getattr__(self, name):
            async def _rec(*a, **k):
                return {"ok": True, "ts": "1.1"}
            return _rec

    # Success path: a normal reply → last status must be `active`.
    ok_ch = "C0STATUSOK"
    clear_session_stopped(ok_ch)
    _cca(ok_ch)

    async def _good_agent(*a, **k):
        return "```taskplan\n{\"mode\":\"plan\",\"title\":\"Thinking\",\"steps\":[{\"id\":\"x\",\"title\":\"Did it\"}]}\n```\nAll set.", "S1"

    sc_ok_client = _StatusClient()
    _orig = _sc_mod.run_agent_offloop
    try:
        _sc_mod.run_agent_offloop = _good_agent
        _asyncio.run(_sc_mod.run_artifact_turn(
            client=sc_ok_client, logger=_log, cfg=_cfg, channel_id=ok_ch,
            task_text="show the metrics", first_turn=False, user_id="U0",
        ))
    finally:
        _sc_mod.run_agent_offloop = _orig
    _cca(ok_ch)
    reset_ok_success = bool(sc_ok_client.statuses) and sc_ok_client.statuses[-1] == "active"

    # Failure path: the agent RAISES → status must STILL end on `active`.
    err_ch = "C0STATUSERR"
    clear_session_stopped(err_ch)
    _cca(err_ch)

    async def _raising_agent(*a, **k):
        raise RuntimeError("boom")

    sc_err_client = _StatusClient()
    try:
        _sc_mod.run_agent_offloop = _raising_agent
        _asyncio.run(_sc_mod.run_artifact_turn(
            client=sc_err_client, logger=_log, cfg=_cfg, channel_id=err_ch,
            task_text="make a snake game", first_turn=True, user_id="U0",
        ))
    finally:
        _sc_mod.run_agent_offloop = _orig
    _cca(err_ch)
    reset_ok_failure = bool(sc_err_client.statuses) and sc_err_client.statuses[-1] == "active"

    status_reset_ok = reset_ok_success and reset_ok_failure
    print(f"[{'PASS' if status_reset_ok else 'FAIL'}] slack_code.run_artifact_turn/status-resets-to-active "
          f"(success={sc_ok_client.statuses}, failure={sc_err_client.statuses})")
    if not status_reset_ok:
        fail += 1

    # A STOPPED session must NOT get a setStatus on exit (Slack owns status then).
    stopreset_ch = "C0STATUSSTOP"
    clear_session_stopped(stopreset_ch)
    _cca(stopreset_ch)
    sc_stop_client = _StatusClient()

    async def _good_agent2(*a, **k):
        mark_session_stopped(stopreset_ch)  # stop arrives during the agent run
        return "done", "S2"

    try:
        _sc_mod.run_agent_offloop = _good_agent2
        _asyncio.run(_sc_mod.run_artifact_turn(
            client=sc_stop_client, logger=_log, cfg=_cfg, channel_id=stopreset_ch,
            task_text="make a snake game", first_turn=True, user_id="U0",
        ))
    finally:
        _sc_mod.run_agent_offloop = _orig
    clear_session_stopped(stopreset_ch)
    _cca(stopreset_ch)
    # After the stop, the finally-block must NOT push `active` (no setStatus at all
    # once stopped — the only status seen should be the pre-stop `processing`).
    no_reset_after_stop = "active" not in sc_stop_client.statuses
    print(f"[{'PASS' if no_reset_after_stop else 'FAIL'}] slack_code.run_artifact_turn/no-status-after-stop "
          f"(statuses={sc_stop_client.statuses})")
    if not no_reset_after_stop:
        fail += 1

    # ...and on that SAME mid-turn-stop path the Thinking stream must be CLOSED
    # (its loading indicator cleared) even though status is left to Slack. The
    # stream was opened before the agent ran (start) and the stop landed during
    # the run, so the finally-block safety net owes exactly one stopStream. This
    # is the fix for the "Stop agent spinner never stops" gap on a mid-turn stop:
    # closing a chat stream is a chat.* call, allowed after a stop (unlike
    # agents.*). Assert the stream was started and then stopped.
    stream_closed_after_stop = sc_stop_client.stream_calls[-1:] == ["stop"] if sc_stop_client.stream_calls else False
    # And it must be closed EXACTLY once (idempotent stop() — no duplicate
    # chat.stopStream from finalize + the finally both firing).
    closed_once = sc_stop_client.stream_calls.count("stop") == 1
    stream_close_ok = stream_closed_after_stop and closed_once
    print(f"[{'PASS' if stream_close_ok else 'FAIL'}] slack_code.run_artifact_turn/stream-closed-on-mid-turn-stop "
          f"(stream_calls={sc_stop_client.stream_calls})")
    if not stream_close_ok:
        fail += 1

    # Guard the idempotent-close on the SUCCESS path too: finalize closes the
    # stream with the answer, and the finally must NOT stopStream a second time.
    success_closed_once = sc_ok_client.stream_calls.count("stop") == 1
    print(f"[{'PASS' if success_closed_once else 'FAIL'}] slack_code.run_artifact_turn/stream-closed-once-on-success "
          f"(stream_calls={sc_ok_client.stream_calls})")
    if not success_closed_once:
        fail += 1

    # A chunks-mode stream must NEVER be stopped with top-level markdown_text (Slack
    # rejects the mismatch with `streaming_mode_mismatch`, stranding the "Stop agent"
    # indicator — the live bug). The success path appends the Thinking plan via
    # chunks, then settles the spinner to a BARE header (stop with no payload) and
    # posts the reply as a SEPARATE plain chat.postMessage — so the stop mode is
    # `empty` (chunks-compatible; no conflicting markdown_text) and the reply rode a
    # postMessage, not the stream. Assert: chunks appended, stop mode not markdown_text,
    # and the reply was a separate postMessage.
    no_mode_mismatch = sc_ok_client.appended_chunks and sc_ok_client.stop_mode != "markdown_text"
    reply_is_postmessage = sc_ok_client.posted >= 1
    stop_mode_matches = no_mode_mismatch and reply_is_postmessage
    print(f"[{'PASS' if stop_mode_matches else 'FAIL'}] slack_code.stream/no-streaming-mode-mismatch-reply-is-plain "
          f"(appended_chunks={sc_ok_client.appended_chunks}, stop_mode={sc_ok_client.stop_mode}, posted={sc_ok_client.posted})")
    if not stop_mode_matches:
        fail += 1

    # ---- Scripted first turn: Thinking(bare ✓) → diff → reply, in that order -----
    # A diff+dashboard scenario (checkout_incident: no preview) runs the SCRIPTED
    # path in _run_session_body. The settled layout reads as THREE ordered beats:
    # `✓ Thinking` (a bare completed header) → the diff → the reply as a SEPARATE
    # message BELOW the diff. The Thinking spinner is ONE stream, opened before the
    # diff and closed to a BARE header (stop with NO prose). The reply is then a
    # PLAIN chat.postMessage (not a second stream) so it appears fully-formed at once
    # — no streamed "bubble then content" lag and no second Thinking block inside it.
    # So we assert: exactly one stream (start+stop), its stop is empty (bare header),
    # and the reply was posted via chat.postMessage.
    _script_ch = "C0SCRIPTOPEN"
    clear_session_stopped(_script_ch)
    _cca(_script_ch)
    sc_script_client = _StatusClient()

    async def _script_agent(*a, **k):
        sc_script_client.events.append("agent")  # mark WHEN the agent ran vs. setView
        return "Rolled it back and reworked the retry path — diff's in the Code tab.", "S3"

    try:
        _sc_mod.run_agent_offloop = _script_agent
        _asyncio.run(_sc_mod._run_session_body(
            client=sc_script_client, logger=_log, cfg=_cfg,
            task_text="roll back the payment-service deploy and fix the retry storm",
            code_channel_id=_script_ch,
            origin_channel_id="C0ORIGIN", origin_thread_ts="1.0", origin_message_ts="1.0",
            user_id="U0", branch_seed="roll-back",
        ))
    finally:
        _sc_mod.run_agent_offloop = _orig
    clear_session_stopped(_script_ch)
    _cca(_script_ch)
    # ONE Thinking stream (start+stop), closed BARE (empty); the reply is a separate
    # plain postMessage (posted >= 1).
    script_stream_ok = (
        sc_script_client.stream_calls == ["start", "stop"]             # exactly one stream
        and sc_script_client.stop_modes == ["empty"]                   # closed BARE (no prose)
        and sc_script_client.posted >= 1                               # reply is a plain postMessage
    )
    print(f"[{'PASS' if script_stream_ok else 'FAIL'}] slack_code._run_session_body/scripted-thinking-bare-then-plain-reply "
          f"(stream_calls={sc_script_client.stream_calls}, stop_modes={sc_script_client.stop_modes}, posted={sc_script_client.posted})")
    if not script_stream_ok:
        fail += 1

    # ...and (FAST FIRST PAINT) the scripted diff must be published BEFORE the agent
    # runs, so the Code tab appears in a few seconds instead of after the ~60s LLM
    # turn. The pre-built diff/dashboard are agent-independent, so publishing them
    # early is safe; a short dwell (not deferring past the agent) keeps the spinner
    # honest. Assert the FIRST setView precedes the agent event.
    _ev = sc_script_client.events
    diff_before_agent_ok = (
        "agent" in _ev and "setView" in _ev
        and _ev.index("setView") < _ev.index("agent")
    )
    print(f"[{'PASS' if diff_before_agent_ok else 'FAIL'}] slack_code._run_session_body/scripted-diff-published-before-agent "
          f"(events={_ev})")
    if not diff_before_agent_ok:
        fail += 1

    # ...and (the spinner-stops-with-the-diff fix) the Thinking spinner must SETTLE
    # right after the diff — i.e. the FIRST stream_stop precedes the agent event.
    # The regression this guards: the diff was published early but the spinner kept
    # spinning through the whole agent turn (it was finalized only AFTER the agent),
    # so the circle ran on over an already-published diff. Assert first stream_stop
    # < agent.
    spinner_settles_with_diff_ok = (
        "stream_stop" in _ev and "agent" in _ev
        and _ev.index("stream_stop") < _ev.index("agent")
        # and the spinner stop comes after the diff was published (settles ON the diff)
        and "setView" in _ev and _ev.index("setView") < _ev.index("stream_stop")
    )
    print(f"[{'PASS' if spinner_settles_with_diff_ok else 'FAIL'}] "
          f"slack_code._run_session_body/scripted-spinner-settles-with-diff-not-after-agent (events={_ev})")
    if not spinner_settles_with_diff_ok:
        fail += 1

    # ...and on a mid-turn stop of that SAME scripted path, the Thinking stream must
    # still be CLOSED (its loading indicator cleared) — closing a chat stream is a
    # chat.* call, allowed after a stop. The stop lands during the agent run, and the
    # _stopped() guard bars the reply post that follows, so we see exactly the
    # Thinking stream's start+stop and NO setStatus=active (Slack owns status then).
    _script_stop_ch = "C0SCRIPTSTOP"
    clear_session_stopped(_script_stop_ch)
    _cca(_script_stop_ch)
    sc_script_stop_client = _StatusClient()

    async def _script_agent_stops(*a, **k):
        mark_session_stopped(_script_stop_ch)  # stop arrives during the agent run
        return "done", "S4"

    try:
        _sc_mod.run_agent_offloop = _script_agent_stops
        _asyncio.run(_sc_mod._run_session_body(
            client=sc_script_stop_client, logger=_log, cfg=_cfg,
            task_text="roll back the payment-service deploy and fix the retry storm",
            code_channel_id=_script_stop_ch,
            origin_channel_id="C0ORIGIN", origin_thread_ts="1.0", origin_message_ts="1.0",
            user_id="U0", branch_seed="roll-back",
        ))
    finally:
        _sc_mod.run_agent_offloop = _orig
    clear_session_stopped(_script_stop_ch)
    _cca(_script_stop_ch)
    script_stop_close_ok = (
        sc_script_stop_client.stream_calls.count("start") == 1  # only the Thinking stream
        and sc_script_stop_client.stream_calls.count("stop") == 1
        and sc_script_stop_client.stream_calls[-1] == "stop"
        and "active" not in sc_script_stop_client.statuses  # Slack owns status after a stop
    )
    print(f"[{'PASS' if script_stop_close_ok else 'FAIL'}] slack_code._run_session_body/scripted-stream-closed-on-mid-turn-stop "
          f"(stream_calls={sc_script_stop_client.stream_calls}, statuses={sc_script_stop_client.statuses})")
    if not script_stop_close_ok:
        fail += 1

    # ---- Scripted-beat pulse holds are per-action and non-trivial ---------------
    # The scripted beats (/check, /patch, /metrics, /recap) hold the Thinking pulse
    # for a per-action dwell so each beat feels like what it claims to do. Assert the
    # known actions map to their tuned holds, all are clearly longer than the old
    # flat 1.1s, and an unknown action falls back to the default.
    _hold = _sc_mod._pulse_hold_s
    holds_ok = (
        _hold("check") == 5.0 and _hold("patch") == 4.0
        and _hold("metrics") == 2.5 and _hold("recap") == 3.0
        and _hold("something-else") == _sc_mod._PULSE_HOLD_DEFAULT
        and all(_hold(a) >= 2.5 for a in ("check", "patch", "metrics", "recap"))  # none as short as old 1.1s
    )
    print(f"[{'PASS' if holds_ok else 'FAIL'}] slack_code.run_scripted_beat/per-action-pulse-holds "
          f"(check={_hold('check')}, patch={_hold('patch')}, metrics={_hold('metrics')}, "
          f"recap={_hold('recap')}, default={_sc_mod._PULSE_HOLD_DEFAULT})")
    if not holds_ok:
        fail += 1

    # ---- Scripted-beat work runs AFTER the hold (artifact in sync with resolve) --
    # The bug: publishing the artifact (setView/canvas) BEFORE the hold made it pop
    # in while the loading circle was still spinning. Fix: hold FIRST (spinner spins
    # alone), THEN run work() (artifact lands), THEN finalize (circle resolves). We
    # record the order of "hold" (patched _pulse_hold_s) vs "work" vs the finalize
    # "stop", and assert hold precedes work precedes stop.
    _beat_ch = "C0BEATSYNC"
    clear_session_stopped(_beat_ch)
    _beat_events: list[str] = []
    _orig_hold = _sc_mod._pulse_hold_s

    def _hold_probe(action):
        _beat_events.append("hold")
        return 0.0  # no real delay in the test

    async def _beat_work():
        _beat_events.append("work")

    class _BeatClient(_StatusClient):
        async def chat_stopStream(self, *a, **k):
            _beat_events.append("stop")
            return await super().chat_stopStream(*a, **k)

    _sc = _load_sc("checkout_incident")
    _beat_client = _BeatClient()
    try:
        _sc_mod._pulse_hold_s = _hold_probe
        _asyncio.run(_sc_mod.run_scripted_beat(
            client=_beat_client, logger=_log, channel_id=_beat_ch, scenario=_sc,
            action="check", work=_beat_work, verdict_markdown="verdict",
        ))
    finally:
        _sc_mod._pulse_hold_s = _orig_hold
    clear_session_stopped(_beat_ch)
    beat_order_ok = (
        "hold" in _beat_events and "work" in _beat_events and "stop" in _beat_events
        and _beat_events.index("hold") < _beat_events.index("work")   # spinner spins BEFORE artifact
        and _beat_events.index("work") < _beat_events.index("stop")   # artifact BEFORE the circle resolves
    )
    print(f"[{'PASS' if beat_order_ok else 'FAIL'}] slack_code.run_scripted_beat/work-after-hold-in-sync "
          f"(events={_beat_events})")
    if not beat_order_ok:
        fail += 1

    return 0 if fail == 0 else 1


async def _run_probe(channel_id: str) -> int:
    """Live probe of the confidential-beta setView/setProperties shapes whose
    per-method contract Slack does not publish. Run from YOUR OWN Terminal against
    a real code channel the bot is in (host OAuth vars leak inside a nested Claude
    session → 'not_logged_in'):

        SLACK_BOT_TOKEN=xoxb-… python qa.py --probe --channel C0CODECHANNEL

    (When you launch the bot via run.sh, that xoxb is what `slack run` injects.)
    It issues one variant at a time and prints the raw SlackCodeResult (ok / error
    / data) so we CONFIRM a shape rather than guess. Nothing here is destructive:
    setView upserts by view_key (we use throwaway keys) and setProperties replaces
    the context bar with one probe item — re-run a normal session to restore it.

    Two things it cracks:
      1) CANVAS view — does `setView type=canvas` want the structured
         document_content object {"type":"markdown","markdown":…} (as the public
         canvases.create/edit do), a flat `content` string, or something else?
      2) INTERACTIVE context-bar item — publish one item_type:"action" item so you
         can CLICK it live and capture the inbound code_channel_action payload from
         the bot's stderr (see the app_mention/events logs).
    """
    import os as _os

    from slack_sdk.web.async_client import AsyncWebClient

    from agent import slackcode

    token = _os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("probe: SLACK_BOT_TOKEN not set. Run from your Terminal with the bot's xoxb "
              "(the token `slack run` injects), not inside a nested Claude session.", file=sys.stderr)
        return 1
    client = AsyncWebClient(
        base_url=_os.environ.get("SLACK_API_URL", "https://slack.com/api"),
        token=token,
    )

    # Same probe logic the in-bot keyword hook uses (run_view_probe), so both
    # paths exercise identical shapes. Prints each result line.
    import logging as _logging

    from listeners.events.slack_code import run_view_probe

    _log = _logging.getLogger("qa-probe")
    lines = await run_view_probe(client, _log, channel_id)
    for ln in lines:
        print(ln)
    if any("is_code_channel=False" in ln for ln in lines):
        print("probe: not a code channel — open one via an @mention and pass its id.", file=sys.stderr)
        return 1
    print("\nprobe: if the action item published OK, CLICK 'Probe: click me' in the channel "
          "and watch the bot's stderr for the inbound event (code_channel_action or a slash-style "
          "POST). Record the payload shape, then wire the handler.")
    return 0


async def _vet_one(q: str) -> tuple[bool, str]:
    """Return (passed, reason). Reason is empty when passed."""
    try:
        response_text, _sid = await run_agent(q)
    except Exception as e:
        return False, f"run_agent raised {type(e).__name__}: {e}"

    blocks, tier = extract_blocks(response_text)
    if tier in {"parse_error", "trailing_prose", "truncated"}:
        return False, f"blockkit tier={tier}"
    if blocks is not None:
        violations = validate_blocks(blocks)
        if violations:
            return False, f"blockkit invalid: {violations[:3]}"

    lower = response_text.lower()
    for marker in CHAR_BREAK_MARKERS:
        if marker in lower:
            return False, f"character-break marker: {marker!r}"

    return True, ""


async def _run_vet(questions: list[str]) -> int:
    if not questions:
        print("No questions to vet.", file=sys.stderr)
        return 1
    fails = 0
    for i, q in enumerate(questions, 1):
        passed, reason = await _vet_one(q)
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] Q{i}: {q}")
        if not passed:
            fails += 1
            print(f"        reason: {reason}")
    print(f"\n{len(questions) - fails}/{len(questions)} passed")
    return 0 if fails == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-demo QA harness")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true", help="Run offline fixtures")
    mode.add_argument("--vet-questions", action="store_true", help="Live-vet demo questions")
    mode.add_argument("--probe", action="store_true",
                      help="Live-probe confidential-beta setView/setProperties shapes "
                           "(canvas + interactive context-bar item). Needs --channel and SLACK_BOT_TOKEN.")
    parser.add_argument(
        "--questions",
        help="Override file: pipe-delimited list of questions",
        default=None,
    )
    parser.add_argument(
        "--channel",
        help="Target code-channel id for --probe (a live code channel the bot is in)",
        default=None,
    )
    args = parser.parse_args()

    if args.self_test:
        return _run_self_test()

    if args.vet_questions:
        if args.questions:
            qs = [q.strip() for q in args.questions.split("|") if q.strip()]
        else:
            qs = _load_demo_questions()
        return asyncio.run(_run_vet(qs))

    if args.probe:
        if not args.channel:
            print("--probe requires --channel C0CODECHANNEL", file=sys.stderr)
            return 1
        return asyncio.run(_run_probe(args.channel))

    return 1


if __name__ == "__main__":
    sys.exit(main())
