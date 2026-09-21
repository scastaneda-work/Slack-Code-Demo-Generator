#!/usr/bin/env bash
# Launch the Claude in Slack Demo Simulator bot (Socket Mode via the Slack CLI).
#
# Reads workspace/team/app identity from tokens.json (see tokens.example.json).
# Model auth: the bot drives claude-agent-sdk -> the host `claude` CLI. If your
# ~/.claude/settings.json carries a Bedrock gateway env (internal SEs), we export
# it; otherwise the CLI uses your own `claude` login. No secrets live in this file.
#
# Usage:  SLACK_CODE_ENABLED=1 ./run.sh          # Slack Code + Tag on
#         ./run.sh                                # plain bot (Slack Code gated off)
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ ! -f tokens.json ]; then
  echo "[run.sh] ERROR: tokens.json not found. Copy tokens.example.json to tokens.json and fill it in (see SETUP.md)." >&2
  exit 1
fi

# Pull workspace_id / team_id / the single app id from tokens.json.
read -r WORKSPACE_ID TEAM_ID APP_ID <<EOF
$(python3 -c "
import json
t = json.load(open('tokens.json'))
bots = t.get('bots', {})
app_id = next(iter(bots), '')
print(t.get('workspace_id',''), t.get('team_id',''), app_id)
")
EOF

if [ -z "$WORKSPACE_ID" ] || [ -z "$APP_ID" ]; then
  echo "[run.sh] ERROR: tokens.json needs workspace_id and a bots[<app_id>] entry (see tokens.example.json)." >&2
  exit 1
fi
NAME="$(python3 -c "import json;b=json.load(open('tokens.json'))['bots'];print(next(iter(b.values())).get('name','Claude AI'))")"

if [ ! -f "$HOME/.slack/credentials.json" ]; then
  echo "[run.sh] ERROR: ~/.slack/credentials.json not found. Run \`slack login\` first (see SETUP.md)." >&2
  exit 1
fi

# ----------------------------------------------------------------------------
# Model auth (OPTIONAL Bedrock gateway). Internal SEs whose ~/.claude/settings.json
# has the Salesforce Bedrock env get it exported here; everyone else falls through
# to their own `claude` CLI login. Never errors on a missing gateway.
# ----------------------------------------------------------------------------
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [ -f "$CLAUDE_SETTINGS" ] && command -v jq >/dev/null 2>&1 && \
   [ -n "$(jq -r '.env.ANTHROPIC_BEDROCK_BASE_URL // empty' "$CLAUDE_SETTINGS")" ]; then
  export CLAUDE_CODE_USE_BEDROCK=$(jq -r '.env.CLAUDE_CODE_USE_BEDROCK // "1"' "$CLAUDE_SETTINGS")
  export ANTHROPIC_BEDROCK_BASE_URL=$(jq -r '.env.ANTHROPIC_BEDROCK_BASE_URL' "$CLAUDE_SETTINGS")
  export ANTHROPIC_AUTH_TOKEN=$(jq -r '.env.ANTHROPIC_AUTH_TOKEN // empty' "$CLAUDE_SETTINGS")
  export ANTHROPIC_MODEL=$(jq -r '.env.ANTHROPIC_MODEL // empty' "$CLAUDE_SETTINGS")
  export NODE_EXTRA_CA_CERTS=$(jq -r '.env.NODE_EXTRA_CA_CERTS // empty' "$CLAUDE_SETTINGS")
  export CLAUDE_CODE_SKIP_BEDROCK_AUTH=$(jq -r '.env.CLAUDE_CODE_SKIP_BEDROCK_AUTH // "1"' "$CLAUDE_SETTINGS")
  echo "[run.sh] Bedrock gateway env loaded from settings.json (model=${ANTHROPIC_MODEL:-unset})"
else
  echo "[run.sh] No Bedrock gateway env found — using your local \`claude\` CLI login."
fi
# Raise the Agent SDK initialize handshake timeout (ms); no-op when init is fast.
export CLAUDE_CODE_STREAM_CLOSE_TIMEOUT="${CLAUDE_CODE_STREAM_CLOSE_TIMEOUT:-180000}"

# Background: after the CLI boots, reconcile the live manifest against manifest.json
# (re-add code_channels + bot scopes/events the CLI's manifest hook strips) and set
# the display name. Best-effort; never blocks the run.
(
  sleep 10
  TOKEN=$(WORKSPACE_ID="$WORKSPACE_ID" python3 -c "
import json, os, sys
creds = json.load(open(os.path.expanduser('~/.slack/credentials.json')))
ws = os.environ['WORKSPACE_ID']
if ws not in creds:
    sys.exit(f'workspace {ws} missing from ~/.slack/credentials.json — run: slack login')
print(creds[ws]['token'])
") || exit 0
  python3 - "$APP_ID" "$NAME" "$TOKEN" <<'PYEOF'
import json, sys, urllib.request, urllib.parse
app_id, name, token = sys.argv[1], sys.argv[2], sys.argv[3]

def _api(method, params):
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=urllib.parse.urlencode(params).encode(),
        headers={"Authorization": f"Bearer {token}"},
    )
    return json.loads(urllib.request.urlopen(req).read())

live = _api("apps.manifest.export", {"app_id": app_id}).get("manifest", {})
try:
    with open("manifest.json") as f:
        local = json.load(f)
except (OSError, ValueError):
    local = {}
lf = local.get("features", {})
live.setdefault("features", {})
for key in ("code_channels",):
    if key in lf and key not in live["features"]:
        live["features"][key] = lf[key]
try:
    live_scopes = live.setdefault("oauth_config", {}).setdefault("scopes", {}).setdefault("bot", [])
    for s in local.get("oauth_config", {}).get("scopes", {}).get("bot", []):
        if s not in live_scopes:
            live_scopes.append(s)
except Exception:
    pass
try:
    live_events = live.setdefault("settings", {}).setdefault("event_subscriptions", {}).setdefault("bot_events", [])
    for e in local.get("settings", {}).get("event_subscriptions", {}).get("bot_events", []):
        if e not in live_events:
            live_events.append(e)
except Exception:
    pass
live.setdefault("display_information", {})["name"] = name
live.setdefault("features", {}).setdefault("bot_user", {})["display_name"] = name
resp = _api("apps.manifest.update", {"app_id": app_id, "manifest": json.dumps(live)})
print(f'[run.sh] manifest reconcile: {"ok" if resp.get("ok") else resp}', flush=True)
PYEOF
) &

if [ -n "$TEAM_ID" ]; then
  exec slack run -w "$WORKSPACE_ID" --org-workspace-grant "$TEAM_ID" --force
else
  exec slack run -w "$WORKSPACE_ID" --force
fi
