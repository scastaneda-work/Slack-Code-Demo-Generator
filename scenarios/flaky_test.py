"""Scenario: fix the flaky login test for real (acme/web-app).

The everyday-engineer story — low stakes, universally relatable, great for a
short demo. Backstory (seeded by ``channels/seed_flaky_test.py``): the
``login_redirect`` end-to-end test fails ~1 in 5 CI runs. Everyone just hits
"re-run" and moves on; it's eroding trust in the suite and burning CI minutes.
The obvious next action:

    @Claude the login_redirect test is flaky — find the root cause and fix it for real

opens the code channel. This module is the SINGLE source of truth for every
artifact that session shows.

Beats (same arc, everyday flavor):
  - "fix it"           → the Code diff (real root-cause fix)
  - "run the tests"    → /run-tests (the suite goes green)
  - "run a check"      → a flake-stability check (many consecutive runs)
  - "is it working"    → a flake-rate dashboard (before → after)
  - "recap it"         → a short 5-section recap

The authored flaw the check catches (must be real, not hand-wavy): the *base*
diff looks like a fix — it replaces a bare ``sleep(200)`` with a longer
``waitFor(500)`` — but it's STILL time-based. On a slow CI runner the redirect
occasionally takes >500ms and the test still flakes, just less often (the most
seductive kind of "fix": it makes the flake rarer, so it looks solved). The flake
-stability check runs it 200× and still catches failures. The patch removes timing
entirely: it waits on the actual post-login condition (the URL/DOM assertion),
so it passes regardless of how slow the runner is.
"""
from __future__ import annotations

# --- Identity / context-bar facts --------------------------------------------
SLUG = "flaky_test"
REPO = "acme/web-app"
REPO_URL = "https://github.com/acme/web-app"
BRANCH_PREFIX = "fix/"
PR_URL = "https://github.com/acme/web-app/pull/2287"

# The check this scenario runs → /check-flake-stability + the context-bar pass item.
CHECK_LABEL = "flake-stability"

# A test fix has no page to preview; its honest artifacts are the code diff + the
# flake-rate dashboard. "diff" is always published.
ARTIFACTS = {"diff", "dashboard"}


# --- Code tab: the diff (pre-patch and post-patch) ---------------------------
def base_diff() -> str:
    """First diff: replace the bare sleep with a longer fixed wait. Looks like a
    fix and cuts the flake rate — but it's STILL time-based, so a slow runner
    still flakes (the flaw the stability check catches)."""
    return (
        "diff --git a/e2e/auth/login_redirect.spec.ts b/e2e/auth/login_redirect.spec.ts\n"
        "--- a/e2e/auth/login_redirect.spec.ts\n"
        "+++ b/e2e/auth/login_redirect.spec.ts\n"
        "@@ -8,13 +8,13 @@ test('logs in and redirects to the dashboard', async ({ page }) => {\n"
        "   await page.fill('#email', 'demo@acme.test');\n"
        "   await page.fill('#password', 'hunter2');\n"
        "   await page.click('button[type=submit]');\n"
        "\n"
        "-  // The redirect is async; give it a moment. Flaky under CI load.\n"
        "-  await page.waitForTimeout(200);\n"
        "+  // Give the redirect more time before asserting. Should be enough.\n"
        "+  await page.waitForTimeout(500);\n"
        "\n"
        "   expect(page.url()).toContain('/dashboard');\n"
        "   expect(await page.textContent('h1')).toBe('Welcome back');\n"
        " });\n"
    )


def patch_diff() -> str:
    """Patched diff after the stability check fails. Removes the fixed timeout
    entirely and waits on the actual post-login condition (navigation to
    /dashboard, then the heading), so it passes regardless of runner speed."""
    return (
        "diff --git a/e2e/auth/login_redirect.spec.ts b/e2e/auth/login_redirect.spec.ts\n"
        "--- a/e2e/auth/login_redirect.spec.ts\n"
        "+++ b/e2e/auth/login_redirect.spec.ts\n"
        "@@ -8,13 +8,14 @@ test('logs in and redirects to the dashboard', async ({ page }) => {\n"
        "   await page.fill('#email', 'demo@acme.test');\n"
        "   await page.fill('#password', 'hunter2');\n"
        "   await page.click('button[type=submit]');\n"
        "\n"
        "-  // Give the redirect more time before asserting. Should be enough.\n"
        "-  await page.waitForTimeout(500);\n"
        "-\n"
        "-  expect(page.url()).toContain('/dashboard');\n"
        "-  expect(await page.textContent('h1')).toBe('Welcome back');\n"
        "+  // Wait on the actual condition, not a timer: the redirect completing and\n"
        "+  // the dashboard heading rendering. Passes no matter how slow the runner is.\n"
        "+  await page.waitForURL('**/dashboard');\n"
        "+  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();\n"
        " });\n"
    )


# --- Check verdicts: the flake-stability fail → patch → clean pass loop -------
def check_fail_report() -> str:
    return (
        ":red_circle: *Flake-stability check — still flaky*\n"
        "• Ran `login_redirect` *200×* on a throttled CI runner. The longer `waitForTimeout(500)` "
        "helped, but it's *still a fixed timer* — when the redirect takes >500ms it fails anyway.\n"
        "• *7 / 200 runs failed* (3.5%). Better than the old ~19%, but not fixed — and it'll come back "
        "on a slow shard.\n"
        "> A time-based wait can only ever make a flake rarer, never gone.\n"
        "Want me to patch it? I'll drop the timer and wait on the real condition (the `/dashboard` "
        "navigation + the heading), which doesn't depend on runner speed."
    )


def check_pass_report() -> str:
    return (
        ":large_green_circle: *Flake-stability check — clean pass*\n"
        "• Same throttled runner, *200 consecutive runs, 0 failures*.\n"
        "• The test now waits on the actual `/dashboard` navigation + the heading being visible — no "
        "timers, so a slow runner just waits a few ms longer instead of failing.\n"
        "• No arbitrary sleeps left in the spec.\n"
        "0 issues. Safe to merge."
    )


# --- Dashboard tab: the flake numbers (Block Kit) -----------------------------
DASHBOARD_ROWS: list[tuple[str, str, str, str]] = [
    # (label, before, after, delta)
    ("login_redirect flake rate", "19%", "*0%*", ":arrow_down: -100%"),
    ("CI re-runs / week (this test)", "~31", "*0*", ":arrow_down: eliminated"),
    ("CI minutes wasted / week", "~210", "*0*", ":arrow_down: -100%"),
    ("Suite trust (green = real)", "\"just re-run it\"", "*trusted*", ":white_check_mark:"),
]


def dashboard_blocks() -> list[dict]:
    """The Dashboard tab as a Block Kit blocks list (real array via blocks=)."""
    rows = "\n".join(
        f"• *{label}*  `{before}` → {after}   _{delta}_"
        for (label, before, after, delta) in DASHBOARD_ROWS
    )
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "login_redirect — flake rate (before → after)"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": rows}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "Last 50 CI runs · `acme/web-app` · scoped to channel members"},
        ]},
    ]


# --- Recap tab: the 5-slide recap (canvas markdown) ---------------------------
def recap_canvas() -> str:
    return "\n\n".join([
        "## 1 · The ask",
        "The `login_redirect` end-to-end test failed ~1 in 5 CI runs. The team had stopped trusting it "
        "— every red build got a reflexive \"just re-run it\" — which hides *real* failures and burns "
        "CI time.",

        "## 2 · What shipped",
        f"Fixed the root cause in `e2e/auth/login_redirect.spec.ts` ({REPO}). Draft PR: {PR_URL}.",

        "## 3 · Caught the fake fix",
        "The first pass just lengthened the wait (`200ms → 500ms`) — a flake-stability run over 200 "
        "iterations showed it *still failed 3.5%* on a slow runner, because it was still a timer. "
        "Patched to wait on the actual condition (navigation + heading). Re-ran 200×: *0 failures*.",

        "## 4 · It's working",
        "• Flake rate: `19%` → *0%* (200 consecutive green)\n"
        "• CI re-runs/wk for this test: `~31` → *0*\n"
        "• CI minutes wasted/wk: `~210` → *0*",

        "## 5 · Next steps",
        "Merge the PR, then sweep the suite for other `waitForTimeout(...)` calls (same anti-pattern) "
        "and add a lint rule that flags fixed sleeps in e2e specs so flakes can't creep back in.",
    ])


# --- Decision provenance: injected into the session so follow-ups recall it ---
def provenance() -> dict[str, str]:
    return {
        "condition_not_timer": (
            "I replaced the fixed wait with a condition-based wait (waitForURL + the heading assertion) "
            "because any timer only makes a flake rarer, never gone — a slow CI shard will always "
            "eventually exceed a hardcoded delay. Waiting on the real post-login state passes regardless "
            "of runner speed."
        ),
        "why_not_just_bump_the_timeout": (
            "Bumping 200ms → 500ms (or 5s) looks like a fix and cuts the failure rate, which is exactly "
            "why it's dangerous: it masks the flake instead of removing it, and it slows every run. The "
            "stability check over 200 iterations is what exposes that it isn't actually fixed."
        ),
        "root_cause": (
            "The redirect is asynchronous; the test asserted the URL before navigation reliably "
            "completed. The bug was in the test's synchronization, not the app — so the fix belongs in "
            "how the test waits, not in adding a sleep to the product."
        ),
        "lint_rule": (
            "Proposed a lint rule banning waitForTimeout in e2e specs so this whole class of timing "
            "flake can't be reintroduced by the next person under deadline pressure."
        ),
    }


def render_provenance() -> str:
    lines = ["Decisions you made on this flaky-test fix and why (use these if asked to explain your choices):"]
    for key, why in provenance().items():
        lines.append(f"- {key}: {why}")
    return "\n".join(lines)


# --- Thinking-pulse lines (see checkout_incident.thinking_line for the pattern). ---
_THINKING = {
    "open": "Reading the thread and reworking the flaky login_redirect wait…",
    "check": "Running login_redirect 200× on a throttled runner to measure the flake rate…",
    "patch": "Rewriting the wait to key off the redirect + heading instead of a timer…",
    "metrics": "Pulling the flake-rate and CI-time numbers from the last 50 runs…",
    "recap": "Writing up the fix — the fake fix, the real root cause, the result…",
}


def thinking_line(action: str) -> str:
    """The Thinking-stream line for a scripted action, or a generic fallback."""
    return _THINKING.get(action, "Working on it…")


# --- Human participants: who carries in from the origin thread, and their beats -
# Adam (the frontend eng who kept hitting re-run on login_redirect) and Ralph (the
# CI owner who tallied the re-run cost) join Claude. Their lines echo the origin
# thread. `name` = spoof username; `email_stem` = invite-tier persona stem.
PARTICIPANTS = [
    {"name": "Adam", "email_stem": "adam_ferris", "role": "frontend eng"},
    {"name": "Ralph", "email_stem": "ralph_clark", "role": "CI owner"},
]


def participant_beats() -> list[dict]:
    """Scripted human chime-ins (arrival = stall; on_artifacts = react to the diff)."""
    return [
        {"name": "Adam", "phase": "arrival",
         "text": (":eyes: Please let this be the end of me hitting re-run on `login_redirect`. "
                  "Watching what Claude finds.")},
        {"name": "Ralph", "phase": "on_artifacts",
         "text": ("~31 re-runs a week just on this one test — if Claude's found the actual race, "
                  "that's real CI minutes back.")},
    ]
