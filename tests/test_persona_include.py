"""load_persona() appends personas/_demo_context.md when it exists.

The template ships a generic Claude persona; a per-demo context file
(personas/_demo_context.md, gitignored, written by the build-slack-demo skill)
is concatenated onto the system prompt so the bot knows the customer's channel
backstory without editing the persona itself.
"""
import importlib
from pathlib import Path

import agent.agent as agent_mod

PERSONAS_DIR = Path(agent_mod.__file__).resolve().parent.parent / "personas"
DEMO_CONTEXT = PERSONAS_DIR / "_demo_context.md"


def test_base_persona_loads_without_demo_context():
    # Ensure no stray _demo_context.md is present for this assertion.
    existed = DEMO_CONTEXT.exists()
    backup = DEMO_CONTEXT.read_text() if existed else None
    if existed:
        DEMO_CONTEXT.unlink()
    try:
        prompt = agent_mod.load_persona("claude_ai")
        assert "Claude" in prompt  # base persona still loads
        assert "MARKER_DEMO_CONTEXT_TEST" not in prompt
    finally:
        if backup is not None:
            DEMO_CONTEXT.write_text(backup)


def test_demo_context_is_appended_when_present():
    existed = DEMO_CONTEXT.exists()
    backup = DEMO_CONTEXT.read_text() if existed else None
    DEMO_CONTEXT.write_text("MARKER_DEMO_CONTEXT_TEST — customer channel backstory here.\n")
    try:
        prompt = agent_mod.load_persona("claude_ai")
        assert "MARKER_DEMO_CONTEXT_TEST" in prompt
        # base persona content is still present (appended, not replaced)
        assert "Claude" in prompt
    finally:
        if backup is not None:
            DEMO_CONTEXT.write_text(backup)
        else:
            DEMO_CONTEXT.unlink()
