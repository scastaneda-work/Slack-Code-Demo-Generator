"""Scenario: Checkout API latency P1 — rollback + retry-storm fix (acme/checkout-service).

The incident story, for an eng-leadership / SRE audience. Backstory (seeded by
``channels/seed_checkout_incident.py``): a 2:14 PM deploy of ``payment-service``
shipped too-aggressive Stripe retry logic; under real traffic the connection pool
saturates, p99 goes 200ms → 3.2s, and orders start failing at payment. Elliott
(VP Eng) raises a P1; Cindy correlates it in Datadog to the deploy. The obvious
next action:

    @Claude roll back the payment-service deploy and fix the retry storm

opens the code channel. This module is the SINGLE source of truth for every
artifact that session shows.

Backend-incident beats (same arc as the video's website story):
  - "ship the fix"              → the Code diff (rollback + bounded retry)
  - "run a check"               → a LOAD TEST that FAILS on a real flaw
  - "generate a patched version"→ the patch closes it; re-check PASSES
  - "is it working"             → a latency/error dashboard (p99, error rate)
  - "write the postmortem"      → a 5-section recap canvas

The authored flaw the check catches (the demo's peak, and it must be real): the
*base* diff reverts the bad deploy and re-adds retries — but the retries are
SYNCHRONOUS with a FIXED 100ms delay and no cap. Under the same load, every
failed Stripe call retries in lockstep, holds its DB connection the whole time,
and the pool saturates again — a retry storm. A load test at peak RPS still shows
p99 blowing out. The patch makes retries bounded with exponential backoff + full
jitter and a circuit breaker that sheds load when Stripe is degraded, so the pool
never saturates.
"""
from __future__ import annotations

# --- Identity / context-bar facts --------------------------------------------
SLUG = "checkout_incident"
REPO = "acme/checkout-service"
REPO_URL = "https://github.com/acme/checkout-service"
BRANCH_PREFIX = "hotfix/"
PR_URL = "https://github.com/acme/checkout-service/pull/914"

# The check this scenario runs → /check-load-test + the context-bar pass item.
CHECK_LABEL = "load-test"

# A production incident has no page to preview; its honest artifacts are the code
# diff + the latency/error dashboard. "diff" is always published.
ARTIFACTS = {"diff", "dashboard"}


# --- Code tab: the diff (pre-patch and post-patch) ---------------------------
def base_diff() -> str:
    """First diff: roll back to the last good handler and re-add retries — but with
    a FIXED-delay, UNBOUNDED, synchronous retry loop that holds the DB connection.
    Under load this re-creates the pool saturation (the retry storm the load test
    catches at demo time)."""
    return (
        "diff --git a/services/payment/charge.py b/services/payment/charge.py\n"
        "--- a/services/payment/charge.py\n"
        "+++ b/services/payment/charge.py\n"
        "@@ -12,18 +12,24 @@\n"
        " def charge(order, conn):\n"
        "     # Roll back v2.4.1's aggressive retry: that shipped a tight retry loop\n"
        "     # with no delay that hammered Stripe and saturated the pool at 2:14pm.\n"
        "-    # v2.4.1 (reverted): retry immediately, forever, until Stripe answers.\n"
        "-    while True:\n"
        "-        resp = stripe.charge(order.token, order.amount_cents)\n"
        "-        if resp.ok:\n"
        "-            return resp\n"
        "-        # no delay, no cap — this is what took us down\n"
        "+    # Restore bounded-ish retries: at most 5 attempts with a fixed 100ms\n"
        "+    # pause between them. Safer than v2.4.1's no-delay loop.\n"
        "+    for attempt in range(5):\n"
        "+        resp = stripe.charge(order.token, order.amount_cents)\n"
        "+        if resp.ok:\n"
        "+            return resp\n"
        "+        time.sleep(0.1)  # fixed backoff; holds `conn` for the whole loop\n"
        "+    raise PaymentError(f\"charge failed after 5 attempts: {resp.error}\")\n"
        "\n"
        "     # NOTE: `conn` (a pooled DB connection) is held for the entire call,\n"
        "     # including every retry + sleep above.\n"
    )


def patch_diff() -> str:
    """Patched diff after the load test fails. Exponential backoff + full jitter so
    retries spread out instead of marching in lockstep, a hard attempt/time budget,
    a circuit breaker that sheds load when Stripe is degraded, and — critically —
    the pooled connection is released BEFORE sleeping so retries don't pin the
    pool."""
    return (
        "diff --git a/services/payment/charge.py b/services/payment/charge.py\n"
        "--- a/services/payment/charge.py\n"
        "+++ b/services/payment/charge.py\n"
        "@@ -12,24 +12,29 @@\n"
        " def charge(order, conn):\n"
        "-    # Restore bounded-ish retries: at most 5 attempts with a fixed 100ms\n"
        "-    # pause between them. Safer than v2.4.1's no-delay loop.\n"
        "-    for attempt in range(5):\n"
        "-        resp = stripe.charge(order.token, order.amount_cents)\n"
        "-        if resp.ok:\n"
        "-            return resp\n"
        "-        time.sleep(0.1)  # fixed backoff; holds `conn` for the whole loop\n"
        "-    raise PaymentError(f\"charge failed after 5 attempts: {resp.error}\")\n"
        "+    # Bounded retry with exponential backoff + full jitter, released\n"
        "+    # connection, and a shared circuit breaker so a Stripe brownout can't\n"
        "+    # saturate the pool the way it did at 2:14pm.\n"
        "+    if not stripe_breaker.allow():\n"
        "+        raise PaymentDegraded(\"stripe circuit open — shedding load\")\n"
        "+    deadline = monotonic() + 2.0            # hard 2s budget per order\n"
        "+    for attempt in range(5):\n"
        "+        resp = stripe.charge(order.token, order.amount_cents)\n"
        "+        if resp.ok:\n"
        "+            stripe_breaker.record_success()\n"
        "+            return resp\n"
        "+        stripe_breaker.record_failure()\n"
        "+        if monotonic() >= deadline:\n"
        "+            break\n"
        "+        # exponential backoff + full jitter; release the pooled conn while we wait\n"
        "+        delay = min(0.05 * (2 ** attempt), 0.8)\n"
        "+        conn.release()\n"
        "+        sleep(random.uniform(0, delay))\n"
        "+        conn.reacquire()\n"
        "+    raise PaymentError(f\"charge failed within budget: {resp.error}\")\n"
    )


# --- Check verdicts: the load-test fail → patch → clean pass loop -------------
def check_fail_report() -> str:
    return (
        ":red_circle: *Load test — FAILED at peak RPS*\n"
        "• Replayed yesterday's 2:14pm traffic (1,900 orders/min) against the rollback. "
        "The fixed-delay retry loop *holds a pooled DB connection through every retry + sleep*, "
        "so when Stripe slows, all workers retry in lockstep and the pool saturates again.\n"
        "• p99 checkout latency climbed to *2.9s* (SLO is 300ms); error rate *6.2%* at the peak minute.\n"
        "> Same failure mode as the incident, just triggered by retries instead of the original loop. "
        "It would fall over again under a real spike.\n"
        "Want me to patch it? I'll switch to exponential backoff + jitter, release the connection while "
        "waiting, and add a circuit breaker to shed load when Stripe is degraded."
    )


def check_pass_report() -> str:
    return (
        ":large_green_circle: *Load test — clean pass*\n"
        "• Same 1,900 orders/min replay: p99 held at *180ms* (under the 300ms SLO), error rate *0.1%*.\n"
        "• Backoff + full jitter spreads retries out; the pooled connection is released while waiting, "
        "so the pool never saturates.\n"
        "• Circuit breaker tripped for *8s* during the injected Stripe brownout and shed load cleanly "
        "instead of melting the pool — orders queued, none dropped.\n"
        "0 issues. Safe to roll forward."
    )


# --- Dashboard tab: the latency/error numbers (Block Kit) ---------------------
# Single source of truth for the numbers so the recap matches exactly.
DASHBOARD_ROWS: list[tuple[str, str, str, str]] = [
    # (label, before, after, delta)
    ("Checkout p99 latency", "3.2s", "*180ms*", ":arrow_down: -94%"),
    ("Payment error rate", "8.0%", "*0.1%*", ":arrow_down: -99%"),
    ("Failed orders / hr (peak)", "~640", "*~7*", ":arrow_down: -99%"),
    ("DB pool saturation events", "37 / hr", "*0*", ":arrow_down: eliminated"),
]


def dashboard_blocks() -> list[dict]:
    """The Dashboard tab as a Block Kit blocks list (real array via blocks=, not a
    json.dumps'd string). mrkdwn bold is single-asterisk."""
    rows = "\n".join(
        f"• *{label}*  `{before}` → {after}   _{delta}_"
        for (label, before, after, delta) in DASHBOARD_ROWS
    )
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "Checkout incident — recovery (during → after)"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": rows}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "Peak-minute replay · `acme/checkout-service` · scoped to channel members"},
        ]},
    ]


# --- Recap tab: the 5-slide postmortem (canvas markdown) ----------------------
def recap_canvas() -> str:
    """5 slide-like sections built from the SAME facts as the dashboard, framed as
    a blameless postmortem for leadership. Canvas markdown: `##` headings,
    single-asterisk bold."""
    return "\n\n".join([
        "## 1 · What happened",
        "A 2:14 PM deploy of `payment-service v2.4.1` shipped a no-delay Stripe retry loop. Under peak "
        "traffic it saturated the checkout DB connection pool — p99 latency `200ms → 3.2s`, orders "
        "failing at payment for *~48 minutes*.",

        "## 2 · What shipped",
        f"Rolled back v2.4.1 and hardened the charge path in {REPO} (hotfix). Draft PR: {PR_URL}.",

        "## 3 · Caught before re-deploy",
        "A load test replaying the 2:14pm peak showed the *rollback alone still failed* — the restored "
        "retry loop held a DB connection through every retry and re-saturated the pool (p99 2.9s). "
        "Patched to exponential backoff + jitter, connection released while waiting, and a circuit "
        "breaker. Re-ran the load test: *clean pass*.",

        "## 4 · It's working",
        "• Checkout p99: `3.2s` → *180ms*\n"
        "• Payment error rate: `8.0%` → *0.1%*\n"
        "• DB pool saturation events: `37/hr` → *0*",

        "## 5 · Follow-ups",
        "Merge the hotfix, keep the latency dashboard pinned through the next peak, add the peak-RPS "
        "load test to CI so a retry regression can't ship again, and add a deploy guardrail that blocks "
        "a merge without a passing load test.",
    ])


# --- Decision provenance: injected into the session so follow-ups recall it ---
def provenance() -> dict[str, str]:
    """decision → rationale, primed into the first agent turn so a later
    '@Claude why …?' answers from recorded reasoning."""
    return {
        "rollback_first": (
            "I rolled back v2.4.1 before anything else to stop the bleeding — restoring the last known "
            "good handler ends the active incident immediately, then we fix the retry logic properly "
            "under a load test rather than hot-patching prod."
        ),
        "backoff_and_jitter": (
            "Fixed-delay retries synchronize: every worker retries at the same instant and stampedes "
            "Stripe + the pool. Exponential backoff with full jitter spreads them out so a Stripe "
            "brownout degrades gracefully instead of saturating the pool."
        ),
        "release_connection": (
            "The original loop pinned a pooled DB connection through every sleep — that's what actually "
            "exhausted the pool. Releasing the connection while waiting means retries don't consume pool "
            "capacity they aren't using."
        ),
        "circuit_breaker": (
            "Added a breaker so when Stripe is clearly degraded we shed load fast (fail a small number "
            "of orders into a retry queue) instead of letting every request pile up and take checkout "
            "down for everyone."
        ),
    }


def render_provenance() -> str:
    lines = ["Decisions you made on this incident fix and why (use these if asked to explain your choices):"]
    for key, why in provenance().items():
        lines.append(f"- {key}: {why}")
    return "\n".join(lines)


# --- Thinking-pulse lines: the ONE honest "Thinking" line shown while a scripted
# beat runs (check/patch/metrics/recap), so Claude visibly processes every turn.
# Story-specific, present-tense, no invented multi-step work. The orchestrator
# opens a one-line stream with this, then finalizes it with the verdict. ---
_THINKING = {
    "open": "Reading the incident thread and drafting the rollback + retry fix…",
    "check": "Replaying the 2:14pm peak traffic against the rollback and watching the DB pool…",
    "patch": "Reworking the retry path — backoff + jitter, releasing the connection, adding the breaker…",
    "metrics": "Pulling the latency + error-rate numbers from the peak-minute replay…",
    "recap": "Writing up the incident — timeline, root cause, fix, follow-ups…",
}


def thinking_line(action: str) -> str:
    """The Thinking-stream line for a scripted action, or a generic fallback."""
    return _THINKING.get(action, "Working on it…")


# --- Human participants: who carries in from the origin thread, and their beats -
# The people already on this incident join Claude in the code channel (Ralph, the
# on-call SRE who insisted on a peak load-test before re-enabling; Elliott, the VP
# who asked for the hotfix PR early). `name` is the spoof username shown in chat;
# `email_stem` is the stable persona stem for the roster-invite tier — the bot
# builds demoeng+<stem>_<orgnum>@slack-corp.com and resolves it (portable: only the
# org number varies). Their code-channel lines echo what they already said upstream.
PARTICIPANTS = [
    {"name": "Ralph", "email_stem": "ralph_clark", "role": "on-call SRE"},
    {"name": "Elliott", "email_stem": "elliott_executive", "role": "VP Eng"},
]


def participant_beats() -> list[dict]:
    """Scripted human chime-ins. `phase`: "arrival" fires as the channel opens (the
    stall while Claude's first reply loads); "on_artifacts" fires after the diff is
    up. Every name is one of PARTICIPANTS."""
    return [
        {"name": "Ralph", "phase": "arrival",
         "text": (":eyes: Following — I want to see the retry path before we re-enable. "
                  "Let's see what Claude comes back with.")},
        {"name": "Elliott", "phase": "on_artifacts",
         "text": ("Diff's up already — nice. Hold it to a load test at today's peak "
                  "before we roll forward.")},
    ]
