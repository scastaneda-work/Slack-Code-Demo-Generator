"""Scenario: add last_login_at end-to-end — safe schema migration (acme/users-service).

The architect / backend-eng story: Claude reasoning across a migration, the model,
the API, and a test — and getting the *operational* safety right, not just the
code. Backstory (seeded by ``channels/seed_sql_migration.py``): product wants
"last seen" on user profiles, which needs a new ``last_login_at`` column on a
large ``users`` table (~24M rows) plus a backfill and API exposure. The obvious
next action:

    @Claude add a last_login_at column to users, backfill it, and expose it on /me

opens the code channel. This module is the SINGLE source of truth for every
artifact that session shows.

Beats (same arc, schema-change flavor):
  - "ship it"            → the Code diff (migration + model + endpoint + test)
  - "run a check"        → a MIGRATION-SAFETY check that FAILS on a real flaw
  - "generate a patch"   → the patch closes it; re-check PASSES
  - "is it working"      → a rollout/backfill dashboard
  - "recap it"           → a 5-section recap

The authored flaw the check catches (must be real — every senior backend eng has
been burned by this): the *base* migration does
``ALTER TABLE users ADD COLUMN last_login_at timestamptz NOT NULL DEFAULT now()``
and a plain ``CREATE INDEX``. On Postgres against a 24M-row table, a NOT NULL +
volatile DEFAULT forces a full table rewrite under an ACCESS EXCLUSIVE lock, and a
non-concurrent CREATE INDEX locks writes — checkout/login would stall for minutes
mid-deploy. The patch splits it into safe steps: add the column nullable (instant,
metadata-only), backfill in batches, add the index CONCURRENTLY, and only then add
the NOT NULL constraint via NOT VALID + VALIDATE — no long lock.
"""
from __future__ import annotations

# --- Identity / context-bar facts --------------------------------------------
SLUG = "sql_migration"
REPO = "acme/users-service"
REPO_URL = "https://github.com/acme/users-service"
BRANCH_PREFIX = "feat/"
PR_URL = "https://github.com/acme/users-service/pull/1043"

# The check this scenario runs → /check-migration-safety + the context-bar item.
CHECK_LABEL = "migration-safety"

# A schema change has no page to preview; its honest artifacts are the code diff +
# the rollout/backfill dashboard. "diff" is always published.
ARTIFACTS = {"diff", "dashboard"}


# --- Code tab: the diff (pre-patch and post-patch) ---------------------------
def base_diff() -> str:
    """First diff: the whole feature in one migration — but the ALTER adds a
    NOT NULL column with a volatile DEFAULT and builds the index non-concurrently.
    On a 24M-row table that's a full rewrite under an exclusive lock (the flaw the
    migration-safety check catches). Also touches the model, the endpoint, and a
    test so the diff reads as a real end-to-end change."""
    return (
        "diff --git a/migrations/0042_add_last_login_at.sql b/migrations/0042_add_last_login_at.sql\n"
        "--- /dev/null\n"
        "+++ b/migrations/0042_add_last_login_at.sql\n"
        "@@ -0,0 +1,6 @@\n"
        "+-- Add last_login_at to users and index it for the profile \"last seen\" feature.\n"
        "+-- One migration, run in the deploy's release phase.\n"
        "+ALTER TABLE users\n"
        "+    ADD COLUMN last_login_at timestamptz NOT NULL DEFAULT now();\n"
        "+\n"
        "+CREATE INDEX idx_users_last_login_at ON users (last_login_at);\n"
        "diff --git a/app/models/user.py b/app/models/user.py\n"
        "--- a/app/models/user.py\n"
        "+++ b/app/models/user.py\n"
        "@@ -14,6 +14,7 @@ class User(Base):\n"
        "     email = Column(String, unique=True, nullable=False)\n"
        "     created_at = Column(DateTime(timezone=True), server_default=func.now())\n"
        "+    last_login_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())\n"
        "diff --git a/app/api/me.py b/app/api/me.py\n"
        "--- a/app/api/me.py\n"
        "+++ b/app/api/me.py\n"
        "@@ -20,6 +20,7 @@ def get_me(user: User = Depends(current_user)):\n"
        "         \"id\": user.id,\n"
        "         \"email\": user.email,\n"
        "+        \"last_login_at\": user.last_login_at.isoformat(),\n"
        "     }\n"
    )


def patch_diff() -> str:
    """Patched migration after the safety check fails. Splits the one dangerous
    ALTER into online-safe steps: nullable add (instant), batched backfill, index
    built CONCURRENTLY, then NOT NULL via NOT VALID + VALIDATE — none of which take
    a long lock on the 24M-row table. Model column becomes nullable to match the
    online rollout; the endpoint guards the null during backfill."""
    return (
        "diff --git a/migrations/0042_add_last_login_at.sql b/migrations/0042_add_last_login_at.sql\n"
        "--- a/migrations/0042_add_last_login_at.sql\n"
        "+++ b/migrations/0042_add_last_login_at.sql\n"
        "@@ -1,6 +1,16 @@\n"
        "-- Add last_login_at to users and index it for the profile \"last seen\" feature.\n"
        "--- One migration, run in the deploy's release phase.\n"
        "-ALTER TABLE users\n"
        "-    ADD COLUMN last_login_at timestamptz NOT NULL DEFAULT now();\n"
        "-\n"
        "-CREATE INDEX idx_users_last_login_at ON users (last_login_at);\n"
        "+-- Online, lock-safe rollout on a 24M-row table (each step is separate):\n"
        "+-- 1) Nullable add is metadata-only — instant, no rewrite, no long lock.\n"
        "+ALTER TABLE users ADD COLUMN last_login_at timestamptz;\n"
        "+-- 2) Backfill in batches out-of-band (see backfill job); keeps locks short.\n"
        "+--    UPDATE users SET last_login_at = created_at WHERE last_login_at IS NULL\n"
        "+--    ORDER BY id LIMIT 10000;  -- repeated until 0 rows\n"
        "+-- 3) Index built CONCURRENTLY so writes to users are never blocked.\n"
        "+CREATE INDEX CONCURRENTLY idx_users_last_login_at ON users (last_login_at);\n"
        "+-- 4) Enforce NOT NULL without a full scan under an exclusive lock:\n"
        "+--    add the constraint NOT VALID, then VALIDATE (a lighter, non-blocking scan).\n"
        "+ALTER TABLE users ADD CONSTRAINT users_last_login_at_not_null\n"
        "+    CHECK (last_login_at IS NOT NULL) NOT VALID;\n"
        "+ALTER TABLE users VALIDATE CONSTRAINT users_last_login_at_not_null;\n"
        "diff --git a/app/jobs/backfill_last_login_at.py b/app/jobs/backfill_last_login_at.py\n"
        "--- /dev/null\n"
        "+++ b/app/jobs/backfill_last_login_at.py\n"
        "@@ -0,0 +1,10 @@\n"
        "+from app.db import session\n"
        "+\n"
        "+# Batched backfill: seed last_login_at from created_at (or auth events) in\n"
        "+# 10k-row chunks so each transaction is short and never holds a long lock.\n"
        "+def backfill(batch=10_000):\n"
        "+    while True:\n"
        "+        n = session.execute(BACKFILL_SQL, {\"batch\": batch}).rowcount\n"
        "+        session.commit()\n"
        "+        if n == 0:\n"
        "+            return\n"
        "diff --git a/app/models/user.py b/app/models/user.py\n"
        "--- a/app/models/user.py\n"
        "+++ b/app/models/user.py\n"
        "@@ -14,7 +14,8 @@ class User(Base):\n"
        "     email = Column(String, unique=True, nullable=False)\n"
        "     created_at = Column(DateTime(timezone=True), server_default=func.now())\n"
        "-    last_login_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())\n"
        "+    # Nullable during the online rollout; NOT NULL enforced in the DB after backfill.\n"
        "+    last_login_at = Column(DateTime(timezone=True), nullable=True)\n"
        "diff --git a/app/api/me.py b/app/api/me.py\n"
        "--- a/app/api/me.py\n"
        "+++ b/app/api/me.py\n"
        "@@ -20,6 +20,7 @@ def get_me(user: User = Depends(current_user)):\n"
        "         \"id\": user.id,\n"
        "         \"email\": user.email,\n"
        "+        \"last_login_at\": user.last_login_at.isoformat() if user.last_login_at else None,\n"
        "     }\n"
    )


# --- Check verdicts: the migration-safety fail → patch → clean pass loop ------
def check_fail_report() -> str:
    return (
        ":red_circle: *Migration-safety check — unsafe on `users` (24M rows)*\n"
        "• `ADD COLUMN last_login_at NOT NULL DEFAULT now()` forces a *full table rewrite under an "
        "ACCESS EXCLUSIVE lock* — every login/checkout write blocks until it finishes.\n"
        "• `CREATE INDEX` (non-concurrent) *locks writes* for the whole build.\n"
        "• Estimated lock time on prod volume: *~3–4 min*. That's a mid-deploy outage on the hot path.\n"
        "> Passes instantly on an empty dev DB, which is exactly why it slips through review.\n"
        "Want me to patch it? I'll split it into online-safe steps: nullable add, batched backfill, "
        "`CREATE INDEX CONCURRENTLY`, then `NOT NULL` via `NOT VALID` + `VALIDATE`."
    )


def check_pass_report() -> str:
    return (
        ":large_green_circle: *Migration-safety check — clean pass*\n"
        "• Nullable `ADD COLUMN` is metadata-only: *lock held < 1ms*, no rewrite.\n"
        "• Backfill runs in *10k-row batches* out-of-band — short transactions, no long lock.\n"
        "• Index built `CONCURRENTLY` — writes to `users` never block.\n"
        "• `NOT NULL` enforced via `NOT VALID` + `VALIDATE` — a lighter scan, no exclusive lock.\n"
        "Max lock on the hot path: *sub-millisecond*. 0 issues. Safe to ship in the release phase."
    )


# --- Dashboard tab: the rollout/backfill numbers (Block Kit) ------------------
DASHBOARD_ROWS: list[tuple[str, str, str, str]] = [
    # (label, before, after, delta)
    ("Max write-lock on `users`", "~3–4 min", "*< 1ms*", ":arrow_down: eliminated"),
    ("Rows backfilled", "0", "*24.1M*", ":white_check_mark: complete"),
    ("Est. login errors during deploy", "~high", "*0*", ":arrow_down: -100%"),
    ("Index build (writes blocked?)", "blocking", "*concurrent*", ":white_check_mark:"),
]


def dashboard_blocks() -> list[dict]:
    """The Dashboard tab as a Block Kit blocks list (real array via blocks=)."""
    rows = "\n".join(
        f"• *{label}*  `{before}` → {after}   _{delta}_"
        for (label, before, after, delta) in DASHBOARD_ROWS
    )
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "last_login_at migration — rollout safety"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": rows}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "Staging replica of prod volume · `acme/users-service` · scoped to channel members"},
        ]},
    ]


# --- Recap tab: the 5-slide recap (canvas markdown) ---------------------------
def recap_canvas() -> str:
    return "\n\n".join([
        "## 1 · The ask",
        "Product wanted \"last seen\" on user profiles — a new `last_login_at` column on the `users` "
        "table (~24M rows), backfilled, and exposed on `/me`. The risk isn't the feature, it's doing "
        "the schema change without locking the login path mid-deploy.",

        "## 2 · What shipped",
        f"Migration + backfill job + model + `/me` field in {REPO}. Draft PR: {PR_URL}.",

        "## 3 · Caught before deploy",
        "The migration-safety check flagged the first pass as an *outage*: a `NOT NULL DEFAULT now()` "
        "add rewrites all 24M rows under an exclusive lock (~3–4 min), and a non-concurrent index locks "
        "writes. Patched to online-safe steps — nullable add, batched backfill, `CREATE INDEX "
        "CONCURRENTLY`, `NOT VALID` + `VALIDATE`. Re-checked: *clean pass, sub-ms lock*.",

        "## 4 · It's working",
        "• Max write-lock on `users`: `~3–4 min` → *< 1ms*\n"
        "• Rows backfilled: *24.1M* (batched, no long lock)\n"
        "• Est. login errors during deploy: `~high` → *0*",

        "## 5 · Next steps",
        "Merge and run in the release phase, drop the temporary null-guard in `/me` once the backfill + "
        "NOT NULL land, and adopt the migration-safety check in CI so an unsafe `ALTER` on a big table "
        "can't be merged again.",
    ])


# --- Decision provenance: injected into the session so follow-ups recall it ---
def provenance() -> dict[str, str]:
    return {
        "nullable_then_backfill": (
            "I added the column nullable first because on Postgres that's a metadata-only change — "
            "instant, no table rewrite. Adding it NOT NULL with a volatile DEFAULT rewrites every row "
            "under an exclusive lock, which on 24M rows is a multi-minute outage on the login path."
        ),
        "index_concurrently": (
            "Built the index with CREATE INDEX CONCURRENTLY so writes to users are never blocked during "
            "the build. A plain CREATE INDEX takes a lock that would stall logins for the whole build."
        ),
        "not_valid_then_validate": (
            "Enforced NOT NULL in two steps — add the CHECK constraint NOT VALID (cheap, doesn't scan), "
            "then VALIDATE separately (a lighter lock that allows concurrent writes) — instead of a "
            "single ALTER that takes an exclusive lock for a full scan."
        ),
        "batched_backfill": (
            "Backfilled in 10k-row batches out-of-band so each transaction is short. One big UPDATE "
            "would hold locks and bloat the WAL; batching keeps the table writable throughout."
        ),
        "null_guard_in_api": (
            "Guarded the null in /me during the rollout window so the endpoint is safe between the "
            "nullable add and the completed backfill — then we remove the guard once NOT NULL is in."
        ),
    }


def render_provenance() -> str:
    lines = ["Decisions you made on this migration and why (use these if asked to explain your choices):"]
    for key, why in provenance().items():
        lines.append(f"- {key}: {why}")
    return "\n".join(lines)


# --- Thinking-pulse lines (see checkout_incident.thinking_line for the pattern). ---
_THINKING = {
    "open": "Reading the thread and drafting the online-safe migration…",
    "check": "Checking the migration's lock behavior against a prod-sized (24M-row) replica…",
    "patch": "Splitting it into online-safe steps — nullable add, batched backfill, concurrent index…",
    "metrics": "Pulling the lock-time and backfill numbers from the staging replica…",
    "recap": "Writing up the migration — the ask, the outage we avoided, the rollout…",
}


def thinking_line(action: str) -> str:
    """The Thinking-stream line for a scripted action, or a generic fallback."""
    return _THINKING.get(action, "Working on it…")


# --- Human participants: who carries in from the origin thread, and their beats -
# Lauren (relayed the "last seen" product ask) and Adam (the backend eng who owns
# the migration) join Claude. Their lines echo the origin thread. `name` = spoof
# username; `email_stem` = invite-tier persona stem.
PARTICIPANTS = [
    {"name": "Lauren", "email_stem": "lauren_bailey", "role": "EM / product"},
    {"name": "Adam", "email_stem": "adam_ferris", "role": "backend eng"},
]


def participant_beats() -> list[dict]:
    """Scripted human chime-ins (arrival = stall; on_artifacts = react to the diff)."""
    return [
        {"name": "Lauren", "phase": "arrival",
         "text": (":eyes: This is the \"last seen\" profile ask from product — keen to see how "
                  "Claude sequences it safely.")},
        {"name": "Adam", "phase": "on_artifacts",
         "text": ("Right — `last_login_at` on `users`. The part I care about is it not locking "
                  "the table on backfill; let's see the check.")},
    ]
