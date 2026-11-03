"""The ./demo terminal UI, driven headless (Textual's run_test + Pilot).

Every test runs against a throwaway repo under tmp_path — its own demos,
personas and tokens.json, an isolated state file and a fake bot — so nothing
touches real demos, Slack, tokens, or ./run.sh. Plain sync tests wrapping
asyncio.run — no pytest-asyncio needed.
"""
import asyncio
import json
import shutil
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Button, Input, OptionList, RichLog, Static  # noqa: E402

from demokit import health, state, trash  # noqa: E402
from demokit.bot import BotProcess  # noqa: E402
from demokit.tui import app as tui_app  # noqa: E402
from demokit.tui.app import SAMPLES, DemoApp  # noqa: E402
from demokit.tui.screens import (  # noqa: E402
    BotSettingsScreen, ConfirmScreen, DeleteScreen, DocScreen, MoreScreen, NewPersonaScreen, OutputScreen,
    RecentlyDeletedScreen, Tick, Toggle,
)

REAL_PERSONA = Path(__file__).resolve().parent.parent / "personas" / "claude_ai.md"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(health, "setup_checks", lambda root=None: [])
    # A real bot running on this Mac (./run.sh) mustn't leak into the tests.
    monkeypatch.setattr(tui_app, "other_bot_pids", lambda mine=None: [])


def _write(root, rel, text=""):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text))
    return p


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    seed = _write(root, "channels/seed_acme.py", """\
        import sys
        print("posting as", " ".join(sys.argv[1:]) or "nobody")
        """)
    _write(root, "demos/acme.toml", f"""\
        name = "Hero refactor"
        customer = "Acme"
        summary = "The hero story."
        scenarios = ["billing_webhook"]
        trigger = "refactor the billing webhook to a durable queue"
        channels = ["acme-hero"]
        files = ["channels/seed_acme.py", "docs/talk.md"]
        docs = [{{ label = "Talk track", path = "docs/talk.md" }}]

        [[actions]]
        id = "post"
        step = "Set the scene"
        label = "Post backstory"
        run = ["{seed}", "--create"]
        options = [{{ flag = "--invite", label = "Also invite the cast" }}]
        repeat_warning = "It posts a second copy."

        [[actions]]
        id = "quick"
        label = "Post one more"
        note = "Adds a single message."
        run = ["{seed}"]

        [[actions]]
        id = "dry"
        label = "Preview"
        note = "Lists what it would post. Nothing in Slack changes."
        run = ["{seed}", "--dry-run"]
        preview = true
        """)
    _write(root, "docs/talk.md", "# talk\n")
    _write(root, "demos/example.toml", 'name = "Built-in example"\nexample = true\n')
    (root / "personas").mkdir()
    shutil.copy(REAL_PERSONA, root / "personas" / "claude_ai.md")
    tokens = root / "tokens.json"
    tokens.write_text(json.dumps({"workspace_id": "W1", "team_id": "T1", "bots": {"A123": {
        "name": "Claude AI", "bot_token": "test-bot-token", "bot_user_id": "U1", "app_token": "test-app-token"}}}))
    tokens.chmod(0o600)
    return root


@pytest.fixture
def fake_bot(tmp_path):
    script = _write(tmp_path, "fake_bot.sh", """\
        #!/usr/bin/env bash
        trap 'exit 0' INT
        echo "bot up, code=$SLACK_CODE_ENABLED tag=$SLACK_TAG_ENABLED"
        while true; do sleep 0.1; done
        """)
    script.chmod(0o755)
    bot = BotProcess(command=(str(script),), cwd=tmp_path)
    yield bot
    bot.stop(grace=0.5)


def _run(coro):
    asyncio.run(coro)


async def _log_text(app, pilot, selector="#output") -> str:
    """The drawer's log. RichLog holds lines back while hidden, so open it first."""
    app.action_activity(show=True)
    app.query_one("#activity-tabs").active = "tab-bot" if selector == "#bot-log" else "tab-output"
    await pilot.pause(0.3)
    return "\n".join(line.text for line in app.query_one(selector, RichLog).lines)


def _page_text(app) -> str:
    return "\n".join(str(w.render()) for w in app.query("#page Static"))


def _status(app) -> str:
    return str(app.query_one("#bot-status", Static).render())


def _button(app, name: str) -> Button:
    return next(b for b in app.query(Button) if b.name == name)


async def _settle(pilot, times=3):
    for _ in range(times):
        await pilot.pause(0.2)


async def _until(pilot, cond, tries=30):
    for _ in range(tries):
        if cond():
            return
        await pilot.pause(0.1)


def test_samples_are_tucked_away_until_opened(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            ol = app.query_one("#demos", OptionList)
            ids = lambda: [ol.get_option_at_index(i).id for i in range(ol.option_count)]  # noqa: E731
            assert "acme" in ids() and SAMPLES in ids() and "example" not in ids()
            assert app.selected == "acme"

            app.selected = SAMPLES
            app.action_next_step()
            await _settle(pilot)
            assert "example" in ids()
            assert "Ready-made stories" in _page_text(app)
    _run(go())


def test_your_demos_are_grouped_by_customer_under_a_header(repo):
    _write(repo, "demos/beta.toml", 'name = "Beta launch"\ncustomer = "Zeta Co"\n')
    _write(repo, "demos/gamma.toml", 'name = "Gamma story"\ncustomer = "Zeta Co"\n')

    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            ol = app.query_one("#demos", OptionList)
            rows = [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]
            assert rows[:3] == ["Your demos", "  Acme  1", "    Hero refactor"]
            assert rows[4:7] == ["  Zeta Co  2", "    Beta launch", "    Gamma story"]
            assert rows[-1].startswith("▸ Sample demos")
            assert app.selected == "acme"  # headers can't be selected
    _run(go())


def test_no_demos_of_your_own_says_so(repo):
    (repo / "demos/acme.toml").unlink()

    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            ol = app.query_one("#demos", OptionList)
            rows = [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]
            assert rows[:2] == ["Your demos", "  None yet"]
    _run(go())


def test_return_runs_the_next_step_with_ticked_options(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.query_one("#opt-post-0", Tick).value = True
            app.query_one("#demos").focus()
            await pilot.press("enter")
            await _until(pilot, lambda: state.last_run("acme", "post"))
            await _settle(pilot)
            assert "posting as --create --invite" in await _log_text(app, pilot)
            assert "Done just now" in _page_text(app)
            # Step 1 is done; the bot is off, so the page points at the top bar's Start bot.
            assert _button(app, "action:post").variant == "default"
            assert "Needs the bot, and it's off" in _page_text(app)
            assert "Show output" in [str(b.label) for b in app.query(Button)]
    _run(go())


def test_running_the_backstory_again_asks_first(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            _button(app, "action:post").press()
            await _until(pilot, lambda: state.last_run("acme", "post"))
            await _settle(pilot)
            _button(app, "action:post").press()
            await _settle(pilot)
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#no")
            await _settle(pilot)
            assert len([r for r in state.load()["runs"] if r["action"] == "post"]) == 1
    _run(go())


def test_secondary_actions_are_links(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            link = _button(app, "action:quick")
            assert "link" in link.classes
            assert "Adds a single message." in _page_text(app)  # every action says what it does
            link.press()
            await _until(pilot, lambda: state.last_run("acme", "quick"))
            await _settle(pilot)
            assert "posting as nobody" in await _log_text(app, pilot)
    _run(go())


def test_responds_in_slack_turns_a_demo_off_and_on(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.query_one("#responds", Toggle).value = False
            await _settle(pilot)
            assert state.hidden_demo_ids() == {"acme"}
            assert "won't respond" in _page_text(app)
            assert "muted" in str(app.query_one("#demos", OptionList).get_option("acme").prompt)
            app.query_one("#responds", Toggle).value = True
            await _settle(pilot)
            assert state.hidden_demo_ids() == set()
    _run(go())


def test_delete_then_restore_from_recently_deleted(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            _button(app, "delete").press()
            await _settle(pilot)
            assert isinstance(app.screen, DeleteScreen)
            await pilot.click("#yes")
            await _settle(pilot)
            assert not (repo / "demos/acme.toml").exists()
            assert [d.id for d in app.demos] == ["example"]

            app.action_recently_deleted()
            await _settle(pilot)
            assert isinstance(app.screen, RecentlyDeletedScreen)
            await pilot.click("#restore")
            await pilot.click("#close")
            await _settle(pilot)
            assert (repo / "demos/acme.toml").exists()
            assert "acme" in [d.id for d in app.demos]
            assert trash.list_trash(repo) == []
            log = await _log_text(app, pilot)
            assert "Deleted “Hero refactor”" in log and "Restored “Hero refactor”" in log
    _run(go())


def test_more_button_in_top_bar_opens_more(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            await pilot.click("#more")
            await _settle(pilot)
            assert isinstance(app.screen, MoreScreen)
    _run(go())


def test_bot_on_off_and_quit_asks_first(repo, fake_bot):
    async def go():
        state.set_bot_features(slack_code=True, tag=False)
        app = DemoApp(root=repo, bot=fake_bot)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("b")
            await _until(pilot, lambda: fake_bot.running)
            await _settle(pilot, 4)
            assert fake_bot.running
            assert "● RUNNING" in _status(app)
            assert str(app.query_one("#bot-toggle", Button).label) == "Stop bot"
            assert "bot up, code=1 tag=0" in await _log_text(app, pilot, "#bot-log")
            assert "The bot is running" in _page_text(app)

            await pilot.press("q")
            await _settle(pilot)
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#no")
            await _settle(pilot)
            assert fake_bot.running

            await pilot.press("b")
            await _until(pilot, lambda: not fake_bot.running)
            await _settle(pilot)
            assert "○ OFF" in _status(app)
    _run(go())


def test_new_persona_and_apply_updates_tokens(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            await pilot.press("a")
            await _settle(pilot)
            assert isinstance(app.screen, BotSettingsScreen)
            await pilot.click("#new-persona")
            await _settle(pilot)
            assert isinstance(app.screen, NewPersonaScreen)
            app.screen.query_one("#p-name", Input).value = "GitHub Copilot"
            app.screen.query_one("#p-maker", Input).value = "GitHub"
            await pilot.click("#yes")
            await _settle(pilot)
            assert isinstance(app.screen, BotSettingsScreen)
            assert app.screen.query_one("#persona-set").pressed_button.name == "github_copilot"
            await pilot.click("#yes")
            await _settle(pilot)

            bot = json.loads((repo / "tokens.json").read_text())["bots"]["A123"]
            assert bot["persona"] == "github_copilot" and bot["name"] == "GitHub Copilot"
            assert bot["bot_token"] == "test-bot-token"  # secrets untouched
            assert (repo / "tokens.json").stat().st_mode & 0o777 == 0o600
            assert "appears in Slack as GitHub Copilot" in _status(app)
            body = (repo / "personas" / "github_copilot.md").read_text()
            assert "You are *GitHub Copilot*, an AI assistant made by GitHub" in body
    _run(go())


def test_the_bot_is_not_a_step_of_any_demo(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            heads = [str(w.render()) for w in app.query("#page .step-head")]
            assert heads == ["[01]  Set the scene", "[02]  Start it in Slack"]
            assert "BOT" in _status(app) and "○ OFF" in _status(app)
            assert str(app.query_one("#bot-toggle", Button).label) == "Start bot"
            assert not [b for b in app.query(Button) if b.name == "bot:on"]  # one Start bot, at the top
    _run(go())


def test_preview_opens_its_output_in_a_window(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            _button(app, "action:dry").press()
            await _until(pilot, lambda: state.last_run("acme", "dry"))
            await _settle(pilot)
            assert isinstance(app.screen, OutputScreen)
            log = "\n".join(line.text for line in app.screen.query_one("#output-log", RichLog).lines)
            assert "posting as --dry-run" in log
            assert "Finished" in str(app.screen.query_one("#output-status", Static).render())
            await pilot.click("#close")
            await _settle(pilot)
            assert not isinstance(app.screen, OutputScreen)
            # It can be opened again from the page.
            _button(app, "output:dry").press()
            await _settle(pilot)
            assert isinstance(app.screen, OutputScreen)
    _run(go())


def test_trigger_phrases_are_listed_next_to_the_switch(repo):
    import scenarios.billing_webhook as code

    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            text = _page_text(app)
            assert "TRIGGER PHRASES" in text
            for kw in code.ROUTE_KEYWORDS:
                assert f"“{kw}”" in text
            assert "a task word" in text  # Slack Code phrases need one
            assert "If you mute it" in text
    _run(go())


def test_channel_links_and_docs_show(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            labels = [str(b.label) for b in app.query(Button)]
            assert "Open #acme-hero ↗" in labels
            assert "Talk track ›" in labels and "DOCS" in _page_text(app)
    _run(go())


def test_a_markdown_doc_opens_in_a_reader(repo):
    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            _button(app, "doc:0").press()
            await _settle(pilot)
            assert isinstance(app.screen, DocScreen)
            assert "Talk track: Hero refactor" in str(app.screen.query_one(".sheet-title", Static).render())
            await pilot.press("escape")
            await _settle(pilot)
            assert not isinstance(app.screen, DocScreen)
    _run(go())


def test_a_bot_started_elsewhere_counts_as_running(repo, monkeypatch):
    monkeypatch.setattr(tui_app, "other_bot_pids", lambda mine=None: [4242])

    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot, 4)
            assert "ANOTHER TERMINAL" in _status(app)
            assert not app.query_one("#bot-toggle", Button).display
            assert "The bot is running" in _page_text(app)
    _run(go())


def test_a_feature_the_demo_needs_being_off_is_called_out(repo):
    async def go():
        state.set_bot_features(slack_code=False, tag=True)
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            assert "Slack Code is turned off for the bot" in _page_text(app)
    _run(go())


def test_banner_shows_when_tall_and_folds_to_one_line_when_short(repo):
    from demokit.tui.banner import Banner

    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            assert app.query_one(Banner).styles.display == "block"
            assert "build, stage and run" in str(app.query_one("#app-title", Static).render())
            await pilot.resize_terminal(80, 24)
            await _settle(pilot)
            assert app.query_one(Banner).styles.display == "none"
            assert "SLACK CODE DEMOS" in str(app.query_one("#app-title", Static).render())
            assert " · as Claude AI" in _status(app)  # the short wording fits 80 columns
    _run(go())


def test_banner_letters_are_even():
    from demokit.tui.banner import FONT, ROWS, SMALL, WORD, art, small_art
    for ch, rows in FONT.items():
        assert len(rows) == ROWS and len({len(r) for r in rows}) == 1, ch
    for ch, rows in SMALL.items():
        assert len({len(r) for r in rows}) == 1, ch
    assert set(WORD) <= FONT.keys() and set(WORD) <= SMALL.keys()
    assert len({len(r) for r in art()}) == 1 and len({len(r) for r in small_art()}) == 1


@pytest.mark.parametrize("cols, big", [(130, True), (100, False)])
def test_banner_spells_slack_code_demos_at_any_width(repo, cols, big):
    from demokit.tui.banner import Banner, fits_big

    async def go():
        app = DemoApp(root=repo)
        async with app.run_test(size=(cols, 44)) as pilot:
            await _settle(pilot)
            banner = app.query_one(Banner)
            assert fits_big(banner.size.width) is big
            assert banner.size.height == (7 if big else 3)
            assert "BETA" not in str(app.query_one("#app-title", Static).render())
    _run(go())


def test_readme_banner_matches_the_app():
    from pathlib import Path

    from demokit.tui.banner import svg
    saved = (Path(__file__).resolve().parent.parent / "docs" / "banner.svg").read_text()
    assert saved == svg(), "regenerate it: .venv/bin/python -m demokit.tui.banner > docs/banner.svg"
