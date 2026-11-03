"""Personas: the bot's identity (name in Slack + voice), one active at a time.

Runs against a throwaway personas/ + tokens.json under tmp_path.
"""
import json
import shutil
from pathlib import Path

import pytest

from agent import persona as agent_persona
from demokit import personas

REAL = Path(__file__).resolve().parent.parent / "personas" / "claude_ai.md"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "personas").mkdir()
    shutil.copy(REAL, tmp_path / "personas" / "claude_ai.md")
    tok = tmp_path / "tokens.json"
    tok.write_text(json.dumps({"bots": {"A9": {"name": "GitHub", "bot_token": "test-bot-token"}}}))
    tok.chmod(0o600)
    return tmp_path


def test_front_matter_is_metadata_not_prompt():
    prompt = agent_persona.load_persona("claude_ai")
    assert "slack_name:" not in prompt and prompt.startswith("# Claude")
    meta, _ = agent_persona.split_front_matter(REAL.read_text())
    assert meta == {"slack_name": "Claude AI"}


def test_unknown_persona_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PERSONA", "does_not_exist")
    assert agent_persona.active_slug() == "claude_ai"


def test_current_flags_a_name_that_doesnt_match_the_voice(root):
    cur = personas.current(root)
    assert (cur.slug, cur.slack_name, cur.app_id) == ("claude_ai", "GitHub", "A9")
    assert not cur.matches(personas.get("claude_ai", root))


def test_create_swaps_only_the_identity(root):
    p = personas.create("GitHub Copilot", "GitHub", root)
    assert (p.slug, p.name, p.slack_name) == ("github_copilot", "GitHub Copilot", "GitHub Copilot")
    body = (root / "personas" / "github_copilot.md").read_text()
    assert "You are *GitHub Copilot*, an AI assistant made by GitHub" in body
    assert "Anthropic" not in body
    assert "Claude Tag" in body  # the product name stays
    with pytest.raises(FileExistsError):
        personas.create("GitHub Copilot", "GitHub", root)
    with pytest.raises(ValueError):
        personas.create("", "GitHub", root)


def test_apply_sets_name_and_voice_together(root):
    personas.create("GitHub Copilot", "GitHub", root)
    personas.apply("github_copilot", root)
    bot = json.loads((root / "tokens.json").read_text())["bots"]["A9"]
    assert bot == {"name": "GitHub Copilot", "bot_token": "test-bot-token", "persona": "github_copilot"}
    assert (root / "tokens.json").stat().st_mode & 0o777 == 0o600
    assert personas.current(root).matches(personas.get("github_copilot", root))
    with pytest.raises(KeyError):
        personas.apply("nope", root)


def test_default_persona_lists_first(root):
    personas.create("Cursor", "Anysphere", root)
    assert [p.slug for p in personas.list_personas(root)] == ["claude_ai", "cursor"]
