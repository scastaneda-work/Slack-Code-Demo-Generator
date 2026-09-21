"""Scenario: Stripe webhook handler → durable queue refactor (acme/billing-service).

A backend Slack Code demo — the diff + reliability dashboard are the heroes (no
page to preview). Routed by keyword ("billing webhook", "durable queue", …) or
forced with SLACK_CODE_SCENARIO=billing_webhook; the default scenario is
website_redesign. Backstory (seeded by
``channels/seed_billing_platform_webhook.py``): the ``/webhooks/stripe`` endpoint
processes events *inline* — a slow handler under a traffic spike blows past the
10s ingress timeout, the connection is cut, and the event is gone (no retry, no
DLQ). Finance finds out at month-end when the numbers don't reconcile. The team
agrees to move processing onto the durable queue the billing jobs already use.

    @Claude refactor the Stripe webhook handler onto the durable queue

is the mention that spins up the code channel. This module is the SINGLE source
of truth for every artifact that session shows, so the diff, the check verdicts,
the dashboard, and the recap can never disagree. (ARTIFACTS = {diff, dashboard} —
a backend refactor has no page, so there's no Preview tab.)

The session beats (a backend refactor's take on the code-channel arc):
  - the code change      → the diff (inline handler → enqueue-on-the-durable-queue)
  - the check that fails → a durability check that FAILS on a real flaw
  - the patched version  → the patch closes the flaw; re-check PASSES
  - is it working?       → a reliability dashboard (dropped events, p95, DLQ)
  - the leadership recap → a 5-section recap

The authored flaw the check catches (this is the demo's emotional peak, so it
must be real, not hand-wavy): the *base* diff still returns 200 to Stripe
BEFORE the enqueue is confirmed durable — ``enqueue(...)`` then ``return "", 200``.
If the process crashes between the enqueue call and the broker's ack, Stripe got
its 200 and won't redeliver, but the event never landed on the queue: still
dropped, just more rarely. The patch flips the order — confirm the enqueue is
durable, THEN 200 — and adds the consumer, so a crash before durability means no
200 and Stripe redelivers.
"""
from __future__ import annotations

# --- Identity / context-bar facts (mirror SlackCodeConfig defaults) ----------
SLUG = "billing_webhook"
REPO = "acme/billing-service"
REPO_URL = "https://github.com/acme/billing-service"
BRANCH_PREFIX = "feat/"
PR_URL = "https://github.com/acme/billing-service/pull/482"

# The check this scenario runs. Drives the slash command name (/check-durability)
# and the context-bar pass item.
CHECK_LABEL = "durability"

# Which artifact tabs this scenario publishes. A backend webhook refactor has no
# page to preview (an HTML "preview" would be a meta-diagram, not a product), so
# this scenario's honest artifacts are the code diff + the reliability dashboard.
# "diff" is always published; listing it here is for clarity.
ARTIFACTS = {"diff", "dashboard"}


# --- Code tab: the diff (pre-patch and post-patch) ---------------------------
def base_diff() -> str:
    """The first diff the Code tab shows. Moves inline processing onto the queue
    — but DELIBERATELY still 200s before the enqueue is durable (the flaw the
    durability check catches at demo time). Extracted from the original
    ``slack_code._fake_diff`` so retargeting the scenario doesn't regress it."""
    return (
        "diff --git a/services/billing/webhooks/stripe.py b/services/billing/webhooks/stripe.py\n"
        "--- a/services/billing/webhooks/stripe.py\n"
        "+++ b/services/billing/webhooks/stripe.py\n"
        "@@ -1,14 +1,15 @@\n"
        "-from billing.handlers import process_event\n"
        "+from billing.queue import enqueue\n"
        "\n"
        " @app.post(\"/webhooks/stripe\")\n"
        " def stripe_webhook(request):\n"
        "     event = verify_signature(request)\n"
        "-    # Process inline — a slow handler under load drops events on timeout,\n"
        "-    # and there is no retry, so a missed payment event is gone for good.\n"
        "-    try:\n"
        "-        process_event(event)\n"
        "-    except Exception:\n"
        "-        log.exception(\"stripe webhook failed\")\n"
        "-    return \"\", 200\n"
        "+    # Ack fast, process durably: enqueue onto the same queue the billing\n"
        "+    # jobs use (retries + DLQ + metrics). Idempotency key = Stripe event id,\n"
        "+    # so redeliveries collapse instead of double-processing.\n"
        "+    enqueue(\"stripe-events\", event, idempotency_key=event.id)\n"
        "+    return \"\", 200\n"
    )


def patch_diff() -> str:
    """The patched diff after the durability check fails. Confirms the enqueue is
    durable BEFORE returning 200 (so a crash before durability means no 200 →
    Stripe redelivers), and registers the ``stripe-events`` consumer that runs the
    already-idempotent ``process_event`` from the queue with retries + DLQ."""
    return (
        "diff --git a/services/billing/webhooks/stripe.py b/services/billing/webhooks/stripe.py\n"
        "--- a/services/billing/webhooks/stripe.py\n"
        "+++ b/services/billing/webhooks/stripe.py\n"
        "@@ -1,10 +1,16 @@\n"
        " from billing.queue import enqueue\n"
        "\n"
        " @app.post(\"/webhooks/stripe\")\n"
        " def stripe_webhook(request):\n"
        "     event = verify_signature(request)\n"
        "-    # Ack fast, process durably: enqueue onto the same queue the billing\n"
        "-    # jobs use (retries + DLQ + metrics). Idempotency key = Stripe event id.\n"
        "-    enqueue(\"stripe-events\", event, idempotency_key=event.id)\n"
        "-    return \"\", 200\n"
        "+    # Durability first: block on the broker ack, THEN 200. If we crash\n"
        "+    # before the enqueue is durable we never 200, so Stripe redelivers —\n"
        "+    # the event can't be lost in the gap between enqueue and broker ack.\n"
        "+    receipt = enqueue(\"stripe-events\", event, idempotency_key=event.id)\n"
        "+    receipt.wait_durable(timeout=2.0)  # raises on non-ack → 5xx → Stripe retries\n"
        "+    return \"\", 200\n"
        "diff --git a/services/billing/queue/consumers.py b/services/billing/queue/consumers.py\n"
        "--- a/services/billing/queue/consumers.py\n"
        "+++ b/services/billing/queue/consumers.py\n"
        "@@ -0,0 +1,8 @@\n"
        "+from billing.handlers import process_event\n"
        "+from billing.queue import consumer\n"
        "+\n"
        "+@consumer(\"stripe-events\", retries=5, dead_letter=\"stripe-events.dlq\")\n"
        "+def handle_stripe_event(event):\n"
        "+    # process_event is already idempotent (keyed on event.id), so retries\n"
        "+    # and redeliveries collapse instead of double-applying an invoice.\n"
        "+    process_event(event)\n"
    )


# --- Check verdicts: the durability fail → patch → clean pass loop ------------
# Scripted + deterministic (the agent does NOT compute these) so the beat is
# identical every run. Posted top-level via chat.postMessage, which always
# renders regardless of the beta / view state. mrkdwn: single-asterisk bold,
# :large_yellow_circle: not :yellow_circle:.
def check_fail_report() -> str:
    return (
        ":red_circle: *Durability check — 1 issue*\n"
        "• `/webhooks/stripe` returns *200 before the enqueue is durable*. "
        "A crash between `enqueue(...)` and the broker ack means Stripe got its 200 and "
        "won't redeliver — the event is silently dropped (rarer than the inline bug, but the "
        "same class of loss).\n"
        "> This is exactly the failure Finance hit at month-end. It ships broken under a spike.\n"
        "Want me to patch it? I'll block on the durable ack before the 200 and add the retrying consumer."
    )


def check_pass_report() -> str:
    return (
        ":large_green_circle: *Durability check — clean pass*\n"
        "• The 200 now waits on `receipt.wait_durable(...)`, so a pre-durability crash returns 5xx and "
        "Stripe *redelivers* — no gap.\n"
        "• `stripe-events` consumer runs the idempotent `process_event` with *5 retries + a DLQ*.\n"
        "• Duplicate/redelivered events collapse on the Stripe event-id idempotency key.\n"
        "0 issues. Safe to ship."
    )


# --- Dashboard tab: the reliability numbers (Block Kit) -----------------------
# The backend-refactor equivalent of the conversion dashboard. Single source of
# truth for the numbers so the recap matches exactly. (label, before, after,
# delta, direction) — direction picks the arrow glyph; "good"/"bad" is narration
# only. No native chart in Block Kit, so KPI rows with arrows.
DASHBOARD_ROWS: list[tuple[str, str, str, str]] = [
    # (label, before, after, delta)
    ("Dropped webhook events / wk", "~12", "*0*", ":arrow_down: -100%"),
    ("Handler p95", "~800ms", "*6ms*", ":arrow_down: -99%"),
    ("DLQ depth (auto-retried)", "n/a", "*3 → drained*", "visible + recoverable"),
    ("Month-end reconciliation", "~4 hrs manual", "*0*", ":arrow_down: eliminated"),
]


def dashboard_blocks() -> list[dict]:
    """The Dashboard tab as a Block Kit blocks list. Passed to ``set_view`` as a
    real JSON ARRAY via ``blocks=`` (NOT a ``json.dumps``'d string — the string
    variant is rejected). mrkdwn bold is single-asterisk."""
    rows = "\n".join(
        f"• *{label}*  `{before}` → {after}   _{delta}_"
        for (label, before, after, delta) in DASHBOARD_ROWS
    )
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "Stripe webhooks — reliability (before → after)"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": rows}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "Last 14 days · `acme/billing-service` · scoped to channel members"},
        ]},
    ]


# --- Recap tab: the 5-slide leadership deck (canvas markdown) -----------------
def recap_canvas() -> str:
    """The Recap tab: 5 slide-like sections built from the SAME facts as the
    dashboard, so leadership sees numbers that match. Canvas markdown: `##`
    section headings, single-asterisk bold."""
    return "\n\n".join([
        "## 1 · The ask",
        "We were silently dropping Stripe webhook events under load — payments marked paid in "
        "Stripe but `open` in our DB. Finance caught it at month-end. Fix it before the next spike, "
        "no war room.",

        "## 2 · What shipped",
        f"Moved `/webhooks/stripe` off inline processing onto the durable queue we already run "
        f"({REPO}). Draft PR: {PR_URL}.",

        "## 3 · Caught before customers",
        "A durability check flagged that the first pass still 200'd *before* the enqueue was durable "
        "— a crash in that gap would still drop the event. Patched to confirm-then-200 with a retrying "
        "consumer + DLQ. Re-checked: *clean pass*.",

        "## 4 · It's working",
        "• Dropped events/wk: `~12` → *0*\n"
        "• Handler p95: `~800ms` → *6ms*\n"
        "• Month-end reconciliation: `~4 hrs manual` → *0*",

        "## 5 · Next steps",
        "Merge the draft PR, watch the DLQ dashboard through the next billing spike, then apply the "
        "same enqueue-first pattern to the remaining inline handlers.",
    ])


# --- Decision provenance: injected into the session so follow-ups recall it ---
def provenance() -> dict[str, str]:
    """Decision → rationale. Rendered into the FIRST agent turn's input so a later
    '@Claude why …?' answers from recorded reasoning, not improvisation."""
    return {
        "enqueue_first_order": (
            "I put the durable-ack wait before the 200 so a crash in the gap can't lose an event: "
            "no ack → no 200 → Stripe redelivers. Acking first is faster but reintroduces the exact "
            "silent-drop class Finance hit."
        ),
        "reuse_existing_queue": (
            "Reused billing.queue.enqueue from the cron + reconciliation work rather than a new "
            "broker — the team already operates it, and process_event is already idempotent, so it's "
            "safe to run from the queue with retries."
        ),
        "idempotency_key": (
            "Keyed idempotency on the Stripe event id so redeliveries and consumer retries collapse "
            "instead of double-applying an invoice."
        ),
        "dlq": (
            "Added a dead-letter queue so a genuinely poisoned event surfaces for on-call instead of "
            "vanishing — the toil Ralph flagged."
        ),
    }


def render_provenance() -> str:
    """A compact block of the provenance facts, for priming the session's first
    agent turn (so a later 'why did you …' answers from these, in the bot's own
    words)."""
    lines = ["Decisions you made on this refactor and why (use these if asked to explain your choices):"]
    for key, why in provenance().items():
        lines.append(f"- {key}: {why}")
    return "\n".join(lines)


# --- Thinking-pulse lines (see checkout_incident.thinking_line for the pattern). ---
_THINKING = {
    "open": "Reading the thread and moving the Stripe handler onto the durable queue…",
    "check": "Checking the handler for durability — does a crash between enqueue and ack drop the event…",
    "patch": "Reordering to confirm-durable-then-200 and adding the retrying consumer + DLQ…",
    "metrics": "Pulling the reliability numbers — dropped events, p95, DLQ depth…",
    "recap": "Writing the leadership recap — the silent drops, the fix, the results…",
}


def thinking_line(action: str) -> str:
    """The Thinking-stream line for a scripted action, or a generic fallback."""
    return _THINKING.get(action, "Working on it…")


# --- Human participants: who carries in from the origin thread, and their beats -
# Ralph (on-call SRE who raised the dropped-webhook toil) and Adam (backend eng who
# dug into the inline handler and owns the refactor) join Claude. Their lines echo
# what they said upstream. `name` = spoof username; `email_stem` = stable persona
# stem for the invite tier (demoeng+<stem>_<orgnum>@slack-corp.com).
PARTICIPANTS = [
    {"name": "Ralph", "email_stem": "ralph_clark", "role": "on-call SRE"},
    {"name": "Adam", "email_stem": "adam_ferris", "role": "backend eng"},
]


def participant_beats() -> list[dict]:
    """Scripted human chime-ins (arrival = stall; on_artifacts = react to the diff)."""
    return [
        {"name": "Ralph", "phase": "arrival",
         "text": (":eyes: Following — a dropped payment event turning into month-end finance "
                  "toil is exactly what I want gone. Curious what Claude proposes.")},
        {"name": "Adam", "phase": "on_artifacts",
         "text": ("That's the inline handler I flagged. If it's moving `process_event` onto a "
                  "queue with retries and a DLQ, that's the fix.")},
    ]
