import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
)
from claude_agent_sdk.types import McpHttpServerConfig

from agent.context import agent_deps_var
from agent.deps import AgentDeps
from agent.tools import add_emoji_reaction_tool

logger = logging.getLogger(__name__)

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"
DEFAULT_PERSONA = "claude_ai"


def load_persona(name: str | None = None) -> str:
    persona_name = name or os.environ.get("PERSONA", DEFAULT_PERSONA)
    path = PERSONAS_DIR / f"{persona_name}.md"
    return path.read_text(encoding="utf-8")


SYSTEM_PROMPT = load_persona()


def _resolve_cli_path() -> str | None:
    """Pick the Claude Code CLI the Agent SDK should drive.

    The SDK prefers its bundled CLI, which lags the host install and sends
    `thinking.type.enabled` — rejected by the Salesforce Bedrock gateway, which
    errors the agent before it produces text. Override with the host CLI.
    Order: $CLAUDE_AGENT_CLI_PATH, then `claude` on PATH, then ~/.local/bin/claude.
    """
    env = os.environ.get("CLAUDE_AGENT_CLI_PATH")
    if env and Path(env).exists():
        return env
    found = shutil.which("claude")
    if found:
        return found
    default = Path.home() / ".local" / "bin" / "claude"
    return str(default) if default.exists() else None


CLI_PATH = _resolve_cli_path()

agent_tools_server = create_sdk_mcp_server(
    name="agent-tools",
    version="1.0.0",
    tools=[add_emoji_reaction_tool],
)

SLACK_MCP_URL = "https://mcp.slack.com/mcp"

AGENT_TOOLS = ["add_emoji_reaction"]

# Model for THIS bot's agent calls. Set here (not via ANTHROPIC_MODEL in
# settings.json) so it scopes to the Claude AI bot only — the other kit bots +
# your own Claude Code keep whatever ANTHROPIC_MODEL the shell exports.
#
# DEFAULT = Sonnet 5. We WANTED Haiku for speed/cost, but a direct probe of the
# preprod SF gateway (team 8f1b249e…, 2026-09-10) showed it is entitled to ONLY
# `us.anthropic.claude-sonnet-5` (+ Sonnet-4); every Haiku 4.5 id 401s ("Team not
# allowed"), and the one Haiku ARN that authenticates silently reroutes to
# Sonnet-4. So there is no real Haiku to switch to on this gateway. Sonnet 5 is
# the fastest clean model available here and never 401-hangs.
#
# Override at launch with CLAUDE_AI_MODEL=<id> the moment Haiku is entitled (or in
# a prod gateway that offers it) — no code edit needed. The startup self-check
# (see log_model_check) surfaces a 401 or a silent reroute in the init log.
CLAUDE_AI_MODEL = os.environ.get("CLAUDE_AI_MODEL", "us.anthropic.claude-sonnet-5")


async def run_agent(
    text: str,
    session_id: str | None = None,
    deps: AgentDeps | None = None,
    user_display_name: str | None = None,
    with_tools: bool = True,
) -> tuple[str, str | None]:
    """Run the agent with the given text and optional session for context.

    Args:
        text: The user's message text.
        session_id: Optional session ID to resume a previous conversation.
        deps: Optional dependencies for tools that need Slack API access.
        user_display_name: Optional sanitized display name to inject into
            the system prompt so the model doesn't ask "who are you?".
        with_tools: Wire the local `agent-tools` SDK MCP server (the emoji
            reaction tool). Default True for DM/channel replies. Code-channel
            sessions pass False: that tool reacts to "the user's current
            message", which is meaningless in a top-level code-channel session,
            AND its SDK-MCP handshake during CLI `initialize` was stalling ~60s
            in that path — dropping it makes init fast and loses nothing there.

    Returns:
        A tuple of (response_text, new_session_id).
    """
    if deps:
        agent_deps_var.set(deps)

    mcp_servers: dict = {"agent-tools": agent_tools_server} if with_tools else {}
    allowed_tools = list(AGENT_TOOLS) if with_tools else []

    if deps and deps.user_token:
        mcp_servers["slack-mcp"] = McpHttpServerConfig(
            type="http",
            url=SLACK_MCP_URL,
            headers={"Authorization": f"Bearer {deps.user_token}"},
        )
        allowed_tools.append("mcp__slack-mcp__*")

    if user_display_name:
        system_prompt = (
            f"The user you are speaking with is *{user_display_name}*. "
            f"Use their first name when natural. Never ask them who they are.\n\n"
            f"{SYSTEM_PROMPT}"
        )
    else:
        system_prompt = SYSTEM_PROMPT

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=CLAUDE_AI_MODEL,  # Haiku for this bot (see CLAUDE_AI_MODEL); overrides ANTHROPIC_MODEL
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        permission_mode="bypassPermissions",
        cli_path=CLI_PATH,  # use host CLI, not the SDK's stale bundled one
        # Capture the CLI subprocess's stderr instead of letting it escape to
        # the terminal unlogged. Without this the SDK swallows the child's own
        # error output, so an initialize-handshake failure looks like a bare
        # "Control request timeout" with no cause. Logged at WARNING so it
        # shows up in the bot log.
        stderr=lambda line: logger.warning("claude-cli stderr: %s", line),
        # Clean boot: skip the full interactive load (SessionStart hooks,
        # ambient MCP servers, plugins) the agent doesn't need. Measured
        # ~60% faster per call (6.9s->2.6s). Does NOT touch the tools we
        # wire explicitly above (mcp_servers/allowed_tools) nor the env
        # (Bedrock auth + ANTHROPIC_MODEL) the shell exports — both survive.
        setting_sources=[],
    )

    if session_id:
        options.resume = session_id

    response_parts: list[str] = []
    new_session_id: str | None = None

    async def _drive() -> None:
        """Init the CLI, send the query, and drain the response stream.

        Timed in two phases so the log distinguishes a slow INIT handshake from a
        stalled GENERATION. Crucially, receive_response() runs to a ResultMessage
        or "continues indefinitely" (SDK docs) if none arrives — so the caller
        wraps this whole coroutine in an overall wall-clock timeout below;
        without it a code channel hangs forever on "processing".
        """
        nonlocal new_session_id
        t0 = time.monotonic()
        async with ClaudeSDKClient(options) as client:
            logger.info("run_agent: CLI init completed in %.1fs", time.monotonic() - t0)
            await client.query(text)
            t1 = time.monotonic()
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            response_parts.append(block.text)
                if isinstance(message, ResultMessage):
                    new_session_id = message.session_id
            logger.info(
                "run_agent: generation completed in %.1fs (%d text block(s))",
                time.monotonic() - t1, len(response_parts),
            )

    # Overall wall-clock ceiling for the entire agent turn (init + generation).
    # The SDK's own timeout only covers the init handshake; generation has none
    # and can hang forever if the CLI never emits a terminal ResultMessage. This
    # guarantees run_agent always returns/raises so a code channel never sticks on
    # "processing". Override via RUN_AGENT_TIMEOUT (seconds); 0/unset disables.
    try:
        overall = float(os.environ.get("RUN_AGENT_TIMEOUT", "300"))
    except ValueError:
        overall = 300.0
    if overall > 0:
        await asyncio.wait_for(_drive(), timeout=overall)
    else:
        await _drive()

    response_text = "\n".join(response_parts) if response_parts else ""
    return response_text, new_session_id


async def run_agent_offloop(
    text: str,
    session_id: str | None = None,
    deps: AgentDeps | None = None,
    user_display_name: str | None = None,
    with_tools: bool = True,
) -> tuple[str, str | None]:
    """Run `run_agent` on a DEDICATED worker thread with its own event loop.

    Why: inside the live Bolt app everything runs on ONE asyncio loop shared with
    the Socket Mode WebSocket (PING/PONG) and the handler's concurrent Slack REST
    calls. The Agent SDK's stdout-read and stderr-drain tasks are scheduled on
    that same loop, so when it is saturated the CLI's response drains slowly and a
    reply that takes ~seconds standalone can take MINUTES in-Bolt. Running the
    whole `run_agent` turn under a fresh `asyncio.run` on a worker thread gives the
    SDK client a private, idle loop — the CLI's I/O is no longer starved.

    Safe for the code-channel path specifically: it calls with `with_tools=False`,
    so nothing inside relies on the `agent_deps_var` ContextVar (which would not
    propagate across the thread boundary). The Slack reply is posted by the CALLER
    on the main loop from the returned text, not from this thread.
    """
    def _worker() -> tuple[str, str | None]:
        return asyncio.run(
            run_agent(
                text,
                session_id=session_id,
                deps=deps,
                user_display_name=user_display_name,
                with_tools=with_tools,
            )
        )

    return await asyncio.to_thread(_worker)


async def log_model_check() -> None:
    """One-shot startup self-check: does the gateway actually serve CLAUDE_AI_MODEL,
    and what does it really run? Logs the configured id and the model the gateway
    returns, so a 401 ("Team not allowed") or a SILENT REROUTE (asking for Haiku,
    getting Sonnet-4) is visible in the init log — not discovered mid-demo as a
    ~90s hang or a wrong-model reply.

    Non-fatal by construction: any error (unreachable gateway, missing env, non-
    Bedrock setup) is logged at WARNING and swallowed. Skips silently when the bot
    isn't on Bedrock (CLAUDE_CODE_USE_BEDROCK unset) or the base URL/token is
    absent. Runs a tiny 1-token invoke against the SAME base URL the CLI uses.
    """
    log = logging.getLogger("claude-ai-bot")
    base = os.environ.get("ANTHROPIC_BEDROCK_BASE_URL")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not (os.environ.get("CLAUDE_CODE_USE_BEDROCK") and base and token):
        log.info("model-check: skipped (not a Bedrock setup or base URL/token unset); model=%s",
                 CLAUDE_AI_MODEL)
        return

    import json
    import ssl
    import urllib.request

    url = f"{base.rstrip('/')}/model/{CLAUDE_AI_MODEL}/invoke"
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )

    def _probe() -> str:
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                actual = (json.loads(r.read()) or {}).get("model")
        except urllib.error.HTTPError as e:
            try:
                msg = (json.loads(e.read()).get("error") or {}).get("message", "")
            except Exception:  # noqa: BLE001
                msg = ""
            return f"HTTP {e.code} — {msg[:160] or 'request rejected'}"
        except Exception as e:  # noqa: BLE001 — never let the check break boot
            return f"probe error: {type(e).__name__}: {str(e)[:120]}"
        if not actual:
            return "OK but no model field in response"
        # Detect a silent reroute: configured a Haiku id, gateway ran something else.
        want = CLAUDE_AI_MODEL.rsplit(".", 1)[-1]  # e.g. "claude-sonnet-5"
        if actual not in want and want not in actual:
            return f":warning: REROUTE — configured {CLAUDE_AI_MODEL!r} but gateway ran {actual!r}"
        return f"OK — running {actual!r}"

    try:
        verdict = await asyncio.to_thread(_probe)
    except Exception as e:  # noqa: BLE001
        verdict = f"check failed: {type(e).__name__}"
    log.warning("model-check: configured=%s → %s", CLAUDE_AI_MODEL, verdict)
