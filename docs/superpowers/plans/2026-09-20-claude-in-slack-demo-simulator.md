# Claude in Slack Demo Simulator — Template Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `claude-ai-bot` from the 7018 monorepo into a standalone, public-shareable repo ("Claude in Slack Demo Simulator") that any Slack SE clones, installs into their own demo org, and customizes into a custom demo via a guided `build-slack-demo` Claude Code skill.

**Architecture:** The extraction is mostly mechanical — copy the bot tree, vendor the 14 `_shared/` symlinks as real files, bring the 5 seed scripts in-repo, and parameterize the handful of org-specific values. The runtime engine (Bolt app + Agent SDK + scenario contract + Slack Code orchestrator) is copied *unchanged*; `qa.py --self-test` proves it survived. New work is: a repo-root `config.py` (cloned from the admin toolkit), a de-hardcoded `run.sh`, four hand-off docs, and one skill.

**Tech Stack:** Python 3 (Bolt for Python / `AsyncApp`, `claude-agent-sdk`, `slack_sdk`), Slack CLI (Socket Mode), host `claude` CLI as the model backend, Claude Code skill (markdown).

**Spec:** `/Users/scastaneda/.claude/plans/i-would-like-to-sunny-unicorn.md`

## Global Constraints

- **Target repo dir:** `/Users/scastaneda/claude-projects/claude-in-slack-demo-simulator/` (new sibling, fresh `git init`). The repo root **is** the bot (no monorepo nesting).
- **No live-org identity may ship.** Forbidden literal values anywhere in tracked files: `E06GQGVDSPP`, `T06GCU6GFEK`, `A0B4Z2RSKRB` (and the other app ids), the audit channel `C0AUC5B30SE`. `7018` and `slack-corp.com` may appear ONLY as documented, overridable defaults or clearly-fictional sample data.
- **No secrets ship.** `git grep -nE 'xox[bp]-'` must match only `PASTE-…` placeholders. `tokens.json`, `.env`, `.slack/apps*.json`, `.slack/cache/`, `*_state.json` gitignored.
- **No leftover symlinks.** `find . -type l` (excluding `.venv`) must return nothing after vendoring.
- **Engine unchanged.** Do not modify scenario contracts (`scenarios/_template.py`, `scenarios_tag/_template.py`, both `__init__.py`), the orchestrator logic, or the Agent SDK wiring except the two narrow parameterizations named below (audit `_mask`, model default). `qa.py --self-test` must pass at every checkpoint after Task 3.
- **Slack mrkdwn = single-asterisk bold** (`*bold*`), never `**bold**`, in any persona/scenario/canvas copy.
- **Model backend:** host `claude` CLI; Bedrock gateway env is OPTIONAL (SE without it falls back to their own `claude` login). Default `CLAUDE_AI_MODEL` must be a public id, not `us.anthropic.*`.
- **Source of truth for copies** (absolute): bot = `/Users/scastaneda/claude-projects/my-slack-demo-org-toolkit-7018/ai-apps-7018/claude-ai-bot/`; shared = `…/ai-apps-7018/_shared/`; seeds = `…/my-slack-demo-org-toolkit-7018/channels/seed_{welloguard_redesign,billing_platform_webhook,checkout_incident,flaky_test,sql_migration}.py`; reference patterns = `/Users/scastaneda/claude-projects/slack-ai-admin-toolkit/{config.py,tokens.example.json,.gitignore,README.md,SETUP.md,HANDOFF.md,CLAUDE.md}` and `/Users/scastaneda/claude-projects/slack-ai-app-simulator/.claude/skills/onboarding-wizard/SKILL.md`.

---

### Task 1: Scaffold the repo — copy the bot tree, vendor symlinks, drop state

**Files:**
- Create: the whole new repo at `/Users/scastaneda/claude-projects/claude-in-slack-demo-simulator/` (copy of the bot tree)
- Modify: replace 14 symlinks with real file copies
- Delete from the copy: `.slack/`, `.slack_code_state.json`, `.tag_state.json`, `.venv/`, all `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `logs/`, `.claude/.cc-writes`, `docs/superpowers/`

**Interfaces:**
- Produces: a standalone tree with real `agent/{audit,blockkit,context,deps,identity,render,slackcode,taskplan,__init__}.py`, `agent/tools/{emoji_reaction,__init__}.py`, `thread_context/store.py`, `listeners/actions/feedback_buttons.py`, `listeners/views/feedback_builder.py` — no symlinks. Later tasks edit `agent/audit.py`, `agent/agent.py`, `run.sh`, add `config.py`.

- [ ] **Step 1: Create the repo dir and copy the bot tree** (exclude derived/state/secret dirs)

```bash
SRC=/Users/scastaneda/claude-projects/my-slack-demo-org-toolkit-7018/ai-apps-7018/claude-ai-bot
DST=/Users/scastaneda/claude-projects/claude-in-slack-demo-simulator
rsync -a --copy-links \
  --exclude '.venv/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.pytest_cache/' --exclude '.ruff_cache/' --exclude 'logs/' \
  --exclude '.slack/' --exclude '.slack_code_state.json' --exclude '.tag_state.json' \
  --exclude '.claude/.cc-writes' --exclude 'docs/superpowers/' \
  "$SRC"/ "$DST"/
```

Note: `--copy-links` resolves the 14 `_shared/` symlinks into real files during the copy — this vendors them in one shot. (The `_shared/` targets live two levels up from the bot, outside `$SRC`; `--copy-links` follows them anyway.)

- [ ] **Step 2: Verify no symlinks remain and the vendored files are real**

Run: `cd $DST && find . -type l -not -path './.venv/*'`
Expected: no output (empty).
Run: `head -1 agent/slackcode.py agent/audit.py thread_context/store.py listeners/views/feedback_builder.py`
Expected: real Python source (not a path), each file non-empty.

- [ ] **Step 3: Confirm the tree shape**

Run: `ls -la && ls agent scenarios scenarios_tag listeners/events`
Expected: `app.py app_oauth.py manifest.json qa.py personas/ scenarios/ scenarios_tag/ agent/ listeners/` present; NO `.slack/`, `.slack_code_state.json`, `.tag_state.json`.

- [ ] **Step 4: Initialize git (no commit yet — .gitignore comes in Task 2)**

```bash
cd $DST && git init -q && echo "initialized"
```

- [ ] **Step 5: Commit the raw extraction**

```bash
cd $DST && git add -A && git commit -q -m "chore: extract claude-ai-bot into standalone repo (symlinks vendored)"
```

---

### Task 2: Secret & config hygiene — config.py, tokens.example.json, .gitignore, .env.sample

**Files:**
- Create: `config.py` (repo root), `tokens.example.json`, `.gitignore`, `.env.sample` (rewrite existing)
- Reference (read, adapt): `slack-ai-admin-toolkit/{config.py,tokens.example.json,.gitignore}`

**Interfaces:**
- Produces: `config.py` exposing `load_tokens()`, `save_tokens()`, `_check_tokens_perms()` (chmod-600 guard), and client factories `app_client()`, `agent_bot_client()`, `user_client(email)`. Seed scripts (Task 5) and setup scripts (Task 6) import these. `DEMO_ADMIN_NAME` env read is consumed by Task 4's `audit.py` edit (not via config.py — audit.py reads env directly to stay dependency-free).

- [ ] **Step 1: Write `config.py`** — clone the admin toolkit's, trimmed to this bot's needs (no `admin_client`/marketplace-admin scope; keep the chmod-600 guard + atomic `save_tokens` verbatim; client factories bound to the bot token and persona user tokens). Keep `SLACK_BASE_URL = "https://www.slack.com/api/"` (sandbox proxy note). Include `agent_bot_client()` returning a `WebClient(token=load_tokens()["bots"][app_id]["bot_token"])`.

- [ ] **Step 2: Write `tokens.example.json`** — placeholder shape:

```json
{
  "team_id": "PASTE-WORKSPACE-TEAM-ID",
  "workspace_id": "PASTE-ENTERPRISE-OR-WORKSPACE-ID",
  "audit_channel_id": "",
  "bots": {
    "PASTE-APP-ID": {
      "name": "Claude AI",
      "bot_token": "xoxb-PASTE-BOT-TOKEN",
      "bot_user_id": "PASTE-BOT-USER-ID",
      "app_token": "xapp-PASTE-APP-TOKEN"
    }
  },
  "users": {}
}
```

- [ ] **Step 3: Write `.gitignore`** — the three-tier contract (adapt admin toolkit's):

```
tokens.json
tokens.json.tmp
.env
# per-SE demo content the builder writes (real names/emails)
personas/_demo_context.md
# runtime state
.slack_code_state.json
.tag_state.json
.slack/apps.json
.slack/apps.dev.json
.slack/cache/
# derived
__pycache__/
*.pyc
.DS_Store
.venv/
.pytest_cache/
.ruff_cache/
logs/
```

- [ ] **Step 4: Rewrite `.env.sample`** — document the public knobs, no secrets: `SLACK_CODE_ENABLED=` (off), `SLACK_CODE_SCENARIO=`, `CLAUDE_AI_MODEL=` (public default, see Task 4), `AUDIT_CHANNEL_ID=`, `DEMO_ADMIN_NAME=`, `SLACK_WORKSPACE_TEAM_ID=`, `SLACK_DEMO_ORG_NUM=`, `SLACK_CODE_FAKE_REPO=`, `SLACK_CODE_CHANNEL_PREFIX=`, and a commented `ANTHROPIC_API_KEY=` fallback line.

- [ ] **Step 5: Verify hygiene**

Run: `cd $DST && python -c "import ast; ast.parse(open('config.py').read()); print('config.py parses')"`
Expected: `config.py parses`
Run: `git grep -nE 'xox[bp]-|xapp-' -- . ':!*.example.json'`
Expected: no matches outside example/doc placeholders.

- [ ] **Step 6: Commit**

```bash
cd $DST && git add -A && git commit -q -m "feat: repo-root config.py with chmod-600 guard, token/env templates, gitignore"
```

---

### Task 3: Prove the engine survived extraction (qa self-test baseline)

**Files:**
- Test: `qa.py` (existing, unchanged) — run `--self-test`
- Possibly Modify: any import that assumed the monorepo layout (only if the self-test surfaces one)

**Interfaces:**
- Consumes: the vendored tree from Task 1. Produces: a green `qa.py --self-test` — the invariant every later task re-checks.

- [ ] **Step 1: Create the venv and install deps**

Run (in the user's Terminal — network needed, PyPI may be sandbox-blocked):
```bash
cd $DST && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```
Expected: clean install.

- [ ] **Step 2: Run the self-test to see it pass (or reveal a layout import bug)**

Run: `cd $DST && .venv/bin/python qa.py --self-test`
Expected: PASS. If it fails on an import referencing `_shared` or the monorepo, fix that import to the local module path and re-run.

- [ ] **Step 3: Commit any import fix (skip if none needed)**

```bash
cd $DST && git add -A && git commit -q -m "fix: resolve imports to standalone layout" || echo "no fix needed"
```

---

### Task 4: Parameterize the two coupled runtime spots — audit mask + model default

**Files:**
- Modify: `agent/audit.py` (the vendored real file) — `_JENNIFER_RE`/`_mask` → env-driven
- Modify: `agent/agent.py:81` — `CLAUDE_AI_MODEL` default to a public id
- Test: `qa.py --self-test` (regression)

**Interfaces:**
- Consumes: `DEMO_ADMIN_NAME` env var (documented in `.env.sample`, Task 2). Produces: audit masking that no-ops when `DEMO_ADMIN_NAME` is unset; a model default any SE's `claude` login can serve.

- [ ] **Step 1: Rewrite `audit.py` `_mask` to be name-generic**

Replace the module-level `_JENNIFER_RE` + `_mask` (audit.py:13-27) with a version that reads `DEMO_ADMIN_NAME` at call time and no-ops when unset:

```python
def _mask(message: str) -> str:
    """Mask a literal demo-admin name in prose to a non-pinging '~Name'.

    The name comes from DEMO_ADMIN_NAME (e.g. the persona whose xoxp token seeds
    the demo); unset → no masking. Skips a name already prefixed with '~' so we
    don't double-tilde one produced by _demention."""
    name = os.environ.get("DEMO_ADMIN_NAME")
    if not name:
        return message
    pattern = re.compile(rf"(?<!~)\b{re.escape(name)}\b")
    return pattern.sub(f"~{name}", message)
```

(Leave `_demention` and `audit_log` untouched — they're already generic.)

- [ ] **Step 2: Default the model to a public id**

Change `agent/agent.py:81` from `os.environ.get("CLAUDE_AI_MODEL", "us.anthropic.claude-sonnet-5")` to a public default. Verify the current public id with the `claude-api` skill before writing it; use that id. Update the surrounding comment to say: default is the public model; internal SEs override with `CLAUDE_AI_MODEL=us.anthropic.claude-sonnet-5` (or rely on their `~/.claude/settings.json`).

- [ ] **Step 3: Verify — self-test still green + audit no-ops without the env**

Run: `cd $DST && .venv/bin/python qa.py --self-test`
Expected: PASS.
Run: `cd $DST && .venv/bin/python -c "import os; os.environ.pop('DEMO_ADMIN_NAME',None); from agent.audit import _mask; print(_mask('Jennifer shipped it'))"`
Expected: `Jennifer shipped it` (unchanged — no masking when unset).

- [ ] **Step 4: Commit**

```bash
cd $DST && git add -A && git commit -q -m "feat: generalize audit masking (DEMO_ADMIN_NAME) and default to public model id"
```

---

### Task 5: De-hardcode `run.sh` and bring seed scripts in-repo

**Files:**
- Modify: `run.sh` — remove the 7018 workspace/app-id block and the hard Bedrock requirement
- Create: `channels/` with the 5 seed scripts + their token bootstrap
- Reference: `…/ai-apps-7018/run.sh:48-124`, `…/channels/seed_welloguard_redesign.py`

**Interfaces:**
- Consumes: `config.py` (Task 2) for token/workspace loading; `.slack/` CLI state at the SE's runtime. Produces: `./run.sh` (no bot-slug arg needed — this repo is Claude-only) and `channels/seed_<slug>.py` scripts runnable as `.venv/bin/python channels/seed_<slug>.py --create`.

- [ ] **Step 1: Rewrite `run.sh`** — single-app launcher. Remove `WORKSPACE_ID=`/`TEAM_ID=` literals and the `case "$BOT"` app-id table; read `workspace_id`/`team_id`/app id from `tokens.json` (via a small `python3 -c` using `config.load_tokens`) or from `.slack/` CLI state. Make the Bedrock block **optional**: if `~/.claude/settings.json` has the gateway env, export it (keep the existing keys); if not, print a one-line "using your local claude login" note and continue (do NOT `exit 1`). Keep `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` default. Preserve the drop-"(local)"-suffix background step.

- [ ] **Step 2: Copy the 5 seed scripts into `channels/`** and rework their header to import the repo-root `config.py`:

```bash
cd $DST && mkdir -p channels
for s in welloguard_redesign billing_platform_webhook checkout_incident flaky_test sql_migration; do
  cp /Users/scastaneda/claude-projects/my-slack-demo-org-toolkit-7018/channels/seed_$s.py channels/
done
```
Then edit each to load tokens via the new `config.py` (replace the toolkit-root `sys.path.insert(...)`/`from config import …` preamble with the standalone equivalent) and to build persona emails from `SLACK_DEMO_ORG_NUM` rather than a hardcoded `7018`.

- [ ] **Step 3: Verify run.sh has no live-org literals and parses**

Run: `cd $DST && bash -n run.sh && echo "run.sh syntax ok"`
Expected: `run.sh syntax ok`
Run: `git grep -nE 'E06GQGVDSPP|T06GCU6GFEK|A0B4Z2RSKRB|C0AUC5B30SE' -- run.sh channels/`
Expected: no matches.

- [ ] **Step 4: Verify seed scripts import cleanly (offline, no Slack calls)**

Run: `cd $DST && for s in channels/seed_*.py; do .venv/bin/python -c "import ast; ast.parse(open('$s').read())" && echo "$s ok"; done`
Expected: each `ok`.

- [ ] **Step 5: Commit**

```bash
cd $DST && git add -A && git commit -q -m "feat: single-app run.sh (optional Bedrock) + in-repo seed scripts"
```

---

### Task 6: Clean the persona — lift the baked-in incident story to a per-demo include

**Files:**
- Modify: `personas/claude_ai.md` — remove demo-specific lines (the Checkout incident channel-context block + the two incident refs)
- Create: `personas/_demo_context.example.md` (the include template; real `_demo_context.md` gitignored per Task 2)
- Verify: how the persona loads the include (check `agent/agent.py` `load_persona`)

**Interfaces:**
- Consumes: nothing new. Produces: a generic Claude persona + an optional `personas/_demo_context.md` include the `build-slack-demo` skill (Task 8) writes. If `load_persona` doesn't already concatenate an include, add a minimal, well-tested concat (append `_demo_context.md` contents when the file exists).

- [ ] **Step 1: Read the persona block to remove**

Run: `sed -n '105,190p' $DST/personas/claude_ai.md`
Expected: shows the "Channel context — Checkout API Latency Incident" section (~109-134) and the incident-referencing sample answers (~170-186).

- [ ] **Step 2: Extract that block into `personas/_demo_context.example.md`** verbatim (it becomes the worked example of a per-demo context include), with a header comment explaining the file is a per-demo include the builder overwrites.

- [ ] **Step 3: Remove the demo-specific block from `claude_ai.md`**, leaving generic Claude Tag behavior. Replace the removed channel-context section with a one-line marker comment: `<!-- Per-demo channel context is injected from personas/_demo_context.md when present. -->`

- [ ] **Step 4: Wire the include (only if not already wired).** Inspect `agent/agent.py:load_persona`. If it doesn't include `_demo_context.md`, add: after reading the persona, if `PERSONAS_DIR / "_demo_context.md"` exists, append its text. Write a test first:

```python
# tests/test_persona_include.py
def test_demo_context_appended(tmp_path, monkeypatch):
    # persona + a _demo_context.md → combined system prompt contains both
    ...
```

- [ ] **Step 5: Run the persona test + self-test**

Run: `cd $DST && .venv/bin/python -m pytest tests/test_persona_include.py -v && .venv/bin/python qa.py --self-test`
Expected: PASS, PASS.

- [ ] **Step 6: Verify no cast names linger in the generic persona**

Run: `git grep -nE 'Elliott|Cindy|Lauren|WelloGuard|Checkout API' -- personas/claude_ai.md`
Expected: no matches (they live only in `_demo_context.example.md` and the sample scenarios).

- [ ] **Step 7: Commit**

```bash
cd $DST && git add -A && git commit -q -m "refactor: generic Claude persona + per-demo context include"
```

---

### Task 7: Hand-off docs — README, SETUP, HANDOFF, CLAUDE.md

**Files:**
- Create: `README.md`, `SETUP.md`, `HANDOFF.md`, `CLAUDE.md` (all repo root; replace the extracted bot README)
- Reference: `slack-ai-admin-toolkit/{README,SETUP,HANDOFF,CLAUDE}.md` for structure

**Interfaces:**
- Consumes: everything above (config, run.sh, skill name). Produces: the SE-facing onboarding surface. `CLAUDE.md` first-run detection points at Task 8's skill.

- [ ] **Step 1: Write `README.md`** — what it is (Claude Tag + Slack Code simulator), the two surfaces explained for a non-coder SE, a "what's in the folder" table, security note (fake artifacts, no real repo/GitHub), and a 3-line quickstart that points to SETUP.md then "ask your Claude Code to build a demo."

- [ ] **Step 2: Write `SETUP.md`** — one-time steps with `[Claude Code]`/`[Terminal]` tags: create the Slack app from `manifest.json`, install to the SE's demo org, capture bot+app tokens (getpass, never in chat) into `tokens.json`, `chmod 600`, venv + `pip install`, request the Slack Code beta for their app. Include a sandbox-gotchas note (`www.slack.com` vs apex, PyPI blocked in-sandbox → run installs in Terminal).

- [ ] **Step 3: Write `HANDOFF.md`** — the lifecycle: clone → SETUP → **build a demo → point at `/build-slack-demo` skill** → run (`SLACK_CODE_ENABLED=1 ./run.sh`) → tear down (archive the demo channel / delete seeded content). Include the `git grep -nE 'xox[bp]-'` pre-commit check and the "what NOT to commit" list.

- [ ] **Step 4: Write `CLAUDE.md`** — instructions for the SE's Claude Code: audience posture (SE, fluent in Slack, not a coder — least-experienced default); first-run detection (no `tokens.json` → walk SETUP.md; configured → offer to run `build-slack-demo`); token discipline; single-asterisk mrkdwn; the pointer to the skill; and a note that Slack Code is a beta the SE's Slack must enable.

- [ ] **Step 5: Verify docs reference real files/commands**

Run: `cd $DST && git grep -l 'build-slack-demo' -- '*.md'`
Expected: HANDOFF.md and CLAUDE.md at least.
Run: `grep -nE 'run\.sh|tokens\.json|qa\.py' SETUP.md HANDOFF.md`
Expected: commands match files that exist in the repo.

- [ ] **Step 6: Commit**

```bash
cd $DST && git add -A && git commit -q -m "docs: README/SETUP/HANDOFF/CLAUDE hand-off suite"
```

---

### Task 8: The `build-slack-demo` skill (the heart)

**Files:**
- Create: `.claude/skills/build-slack-demo/SKILL.md`
- Reference (read): `slack-ai-app-simulator/.claude/skills/onboarding-wizard/SKILL.md` (structure), `scenarios/_template.py` + `scenarios_tag/_template.py` (output contracts), `scenarios/website_redesign.py` + `scenarios/billing_webhook.py` (few-shot examples), `scenarios/__init__.py` (`_KNOWN` + `_ROUTE_KEYWORDS`), `scenarios_tag/__init__.py`.

**Interfaces:**
- Consumes: the scenario contracts (unchanged). Produces: on each run, an SE-authored `scenarios/<slug>.py` and/or `scenarios_tag/<slug>.py`, a `channels/seed_<slug>.py`, `__init__.py` registration edits, and optionally `personas/_demo_context.md` — all passing `qa.py --self-test`.

- [ ] **Step 1: Write the SKILL.md frontmatter + interview flow.** `name: build-slack-demo`, `description:` triggers on "build a demo", "customize the demo for <customer>", "new Slack Code story". The interview, in order:
  1. **Surface branch** — ask: Slack Code, Claude Tag, or both? Only do the chosen path(s).
  2. **Story gather** — customer/company name, product/domain, 1–2 cast members from the origin thread + their real `email_stem`s in the SE's org (ask; never fabricate), the technical change, and the flaw→check→patch beat.
  3. **Artifact menu (Slack Code)** — present Code diff (always), HTML Preview (real rendered page), Dashboard (Block Kit KPIs), Canvas recap. Note Canvas IS supported (the stale "posts as a message" comments are wrong). Let the story suggest the default `ARTIFACTS`; offer ones the SE may not know; SE can override.

- [ ] **Step 2: Write the authoring instructions** — the skill fills `scenarios/_template.py` for a Slack Code story (every member; dashboard & recap numbers from one shared constant; author a real flaw in `base_diff`, fix in `patch_diff`; `preview_html` self-contained, no JS/external assets; participants ≤2 with `email_stem`), and/or `scenarios_tag/_template.py` for a Tag story (keywords disjoint from Slack Code). Registers the slug in the correct `_KNOWN` + `_ROUTE_KEYWORDS`. Writes `channels/seed_<slug>.py` modeled on `seed_welloguard_redesign.py`, planting the flaw and ending with a trigger @-mention containing a routing keyword.

- [ ] **Step 3: Write the verify + handoff instructions** — run `.venv/bin/python qa.py --self-test`; iterate until the new slug passes the contract loop; then print the exact seed + launch commands (`.venv/bin/python channels/seed_<slug>.py --create`, then `SLACK_CODE_ENABLED=1 ./run.sh`), reminding the SE to run them in their own Terminal.

- [ ] **Step 4: Dry-run the skill against a fictional customer (Slack Code path)** — in this session, follow the SKILL.md as written to author a throwaway `scenarios/acme_widget.py` + seed + `__init__.py` edits.

- [ ] **Step 5: Verify the generated scenario passes the contract**

Run: `cd $DST && .venv/bin/python qa.py --self-test`
Expected: PASS, including the new `acme_widget` slug.

- [ ] **Step 6: Remove the throwaway scenario** (it was a skill dry-run, not shipping content) and re-run self-test to confirm clean baseline.

Run: `cd $DST && git checkout -- scenarios/__init__.py && rm -f scenarios/acme_widget.py channels/seed_acme_widget.py && .venv/bin/python qa.py --self-test`
Expected: PASS.

- [ ] **Step 7: Commit the skill**

```bash
cd $DST && git add -A && git commit -q -m "feat: build-slack-demo skill — guided custom-scenario authoring"
```

---

### Task 9: Final sweep + optional publish

**Files:** whole repo (verification only), then optional `gh` push.

- [ ] **Step 1: Run the full de-coupling + hygiene sweep**

Run:
```bash
cd $DST && find . -type l -not -path './.venv/*'; \
git grep -nE 'E06GQGVDSPP|T06GCU6GFEK|A0B4Z2RSKRB|C0AUC5B30SE'; \
git grep -nE 'xox[bp]-|xapp-' -- . ':!*.example.json' ':!*.md'; \
git grep -nE '\bslack-corp\.com\b|\b7018\b' -- . ':!*.md'
```
Expected: symlinks empty; live IDs empty; secrets empty; `slack-corp.com`/`7018` only as documented defaults (review each hit — none may be a live identity).

- [ ] **Step 2: Full self-test once more**

Run: `cd $DST && .venv/bin/python qa.py --self-test`
Expected: PASS.

- [ ] **Step 3: STOP — confirm with the user before any push.** Report the sweep results. Do not push until the user explicitly approves the repo name and owner. Then verify canonical owner with `gh api user` / `gh api` and create the repo private-first.

---

## Self-Review notes

- **Spec coverage:** WS1→Tasks 1,3; WS2→Tasks 4,5; WS3→Tasks 2,4,6; WS4→Tasks 3,8 (+ stale-comment fix folded into Task 8's Canvas note); WS5→Tasks 7,8. All five workstreams covered.
- **Stale recap comments:** addressed as documentation inside the skill (Task 8 Step 1) rather than editing engine comments, to honor the "engine unchanged" constraint; the SE-facing truth (Canvas is real) lives where the SE reads it.
- **Type consistency:** `config.py` factory names (`app_client`, `agent_bot_client`, `user_client`) are used consistently by seed scripts (Task 5) and setup (Task 7). `DEMO_ADMIN_NAME` env name matches between `.env.sample` (Task 2) and `audit.py` (Task 4).
- **Open risk:** Task 3 Step 1 (venv/pip) and any live Slack step must run in the user's own Terminal (PyPI + host OAuth constraints). Flagged in Global Constraints and Task 9.
