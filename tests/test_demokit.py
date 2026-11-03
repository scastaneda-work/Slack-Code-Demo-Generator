"""demokit: manifests, hide state, actions, and the restorable trash.

Each test builds a throwaway repo under tmp_path and points the state file
there, so nothing touches the real demos, scenarios, or .demo_manager_state.json.
"""
import sys
import textwrap

import pytest

from demokit import actions, health, routing, state, trash
from demokit.custom import package_dirs
from demokit.manifest import Action, Option, load_demos, owned_files, parse


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "state.json")


def _write(root, rel, text=""):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text))
    return p


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    _write(root, "scenarios/acme_hero.py", "SLUG = 'acme_hero'\n")
    _write(root, "channels/seed_acme_hero.py", "print('seeded')\n")
    _write(root, "docs/demos/acme-hero/talk-track.md", "# talk\n")
    _write(root, "docs/demos/acme-hero/click-path.html", "<!doctype html>\n")
    _write(root, "scenarios/assets/acme_logo.png", "png")
    _write(root, "scenarios/assets/acme_hero.jpg", "jpg")
    _write(root, "demos/acme-hero.toml", """\
        name = "Acme · Hero"
        customer = "Acme"
        surfaces = ["slack_code"]
        scenarios = ["acme_hero"]
        trigger = "build the acme hero"
        files = ["channels/seed_acme_hero.py", "docs/demos/acme-hero/", "scenarios/assets/acme_*"]

        [[actions]]
        id = "post-backstory"
        label = "Post backstory"
        run = ["channels/seed_acme_hero.py", "--create"]
        options = [{ flag = "--invite", label = "Invite the cast" }]
        """)
    # A second demo that shares the logo.
    _write(root, "demos/acme-other.toml", """\
        name = "Acme · Other"
        customer = "Acme"
        files = ["scenarios/assets/acme_logo.png"]
        """)
    _write(root, "demos/example.toml", 'name = "Built-in"\nexample = true\n')
    return root


@pytest.fixture
def private(repo):
    """A private demo: one folder under demos/custom/ holding all its files."""
    base = "demos/custom/globex-launch"
    _write(repo, f"{base}/scenarios/globex_launch.py", "SLUG = 'globex_launch'\n")
    _write(repo, f"{base}/scenarios/assets/globex_logo.png", "png")
    _write(repo, f"{base}/channels/seed_globex_launch.py", "print('seeded')\n")
    _write(repo, f"{base}/docs/talk-track.md", "# talk\n")
    _write(repo, f"{base}/demo.toml", f"""\
        name = "Globex · Launch"
        customer = "Globex"
        scenarios = ["globex_launch"]
        docs = [{{ label = "Talk track", path = "{base}/docs/talk-track.md" }}]

        [[actions]]
        id = "post-backstory"
        label = "Post backstory"
        run = ["{base}/channels/seed_globex_launch.py", "--create"]
        """)
    return repo


# ---- manifests --------------------------------------------------------------

def test_valid_manifest_parses_without_problems(repo):
    d = parse(repo / "demos" / "acme-hero.toml")
    assert d.problems == ()
    assert d.id == "acme-hero" and d.scenarios == ("acme_hero",)
    assert d.action("post-backstory").options == (Option("--invite", "Invite the cast"),)


def test_bad_manifest_collects_problems_instead_of_raising(tmp_path):
    p = _write(tmp_path, "demos/bad.toml", """\
        surfaces = ["slack_code", "carrier_pigeon"]
        [[actions]]
        id = "a"
        label = "A"
        run = []
        options = [{ flag = "invite" }]
        [[actions]]
        id = "a"
        label = "A again"
        run = ["x.py"]
        """)
    problems = " | ".join(parse(p).problems)
    assert "`name` is required" in problems
    assert "carrier_pigeon" in problems
    assert "`run` must be a non-empty list" in problems
    assert "starting with --" in problems
    assert "unique" in problems


def test_unreadable_toml_is_a_problem_not_a_crash(tmp_path):
    p = _write(tmp_path, "demos/broken.toml", "name = \n")
    d = parse(p)
    assert d.name == "broken" and d.problems


def test_examples_sort_last(repo):
    assert [d.id for d in load_demos(repo)][-1] == "example"


def test_owned_files_expands_dirs_globs_and_scenarios(repo):
    d = parse(repo / "demos" / "acme-hero.toml")
    rel = {str(p.relative_to(repo.resolve())) for p in owned_files(d, repo)}
    assert rel == {
        "demos/acme-hero.toml", "scenarios/acme_hero.py", "channels/seed_acme_hero.py",
        "docs/demos/acme-hero/talk-track.md", "docs/demos/acme-hero/click-path.html",
        "scenarios/assets/acme_logo.png", "scenarios/assets/acme_hero.jpg",
    }


# ---- private demos (demos/custom/<id>/) ---------------------------------------

def test_private_demo_is_listed_with_its_folder_name_as_id(private):
    d = next(d for d in load_demos(private) if d.customer == "Globex")
    assert d.id == "globex-launch" and not d.problems
    assert d.folder == private / "demos/custom/globex-launch"
    assert parse(private / "demos/acme-hero.toml").folder is None


def test_private_demo_owns_its_whole_folder(private):
    d = parse(private / "demos/custom/globex-launch/demo.toml")
    rel = {str(p.relative_to(private.resolve())) for p in owned_files(d, private)}
    base = "demos/custom/globex-launch"
    assert rel == {f"{base}/{f}" for f in ("demo.toml", "scenarios/globex_launch.py",
                                           "scenarios/assets/globex_logo.png",
                                           "channels/seed_globex_launch.py", "docs/talk-track.md")}


def test_private_demo_files_pass_the_health_checks(private):
    demos = load_demos(private)
    d = next(d for d in demos if d.id == "globex-launch")
    labels = {c.label: c.ok for c in health.demo_checks(d, demos, private)}
    assert labels["demos/custom/globex-launch/docs/talk-track.md exists"]
    assert labels["demos/custom/globex-launch/channels/seed_globex_launch.py exists"]
    assert "demos/custom/globex-launch/scenarios/globex_launch.py exists" in labels


def test_delete_then_restore_a_private_demo(private):
    demos = load_demos(private)
    d = next(d for d in demos if d.id == "globex-launch")
    entry = trash.delete(trash.plan_delete(d, demos, private), private)
    assert not (private / "demos/custom/globex-launch").exists()  # whole folder gone
    assert (private / "demos/custom").is_dir()                     # the parent stays
    trash.restore(entry.name, private)
    assert (private / "demos/custom/globex-launch/scenarios/assets/globex_logo.png").exists()


def test_a_shared_and_a_private_demo_with_one_id_is_a_problem(private):
    _write(private, "demos/globex-launch.toml", 'name = "Globex · Launch (old copy)"\n')
    checks = health.repo_checks(load_demos(private), private)
    dup = next(c for c in checks if c.label == "globex-launch has one manifest")
    assert not dup.ok and "demos/globex-launch.toml" in dup.detail


def test_a_private_demos_story_imports_like_a_shared_one(private, monkeypatch):
    import importlib
    import pkgutil
    import scenarios

    assert package_dirs("scenarios", private) == [str(private / "demos/custom/globex-launch/scenarios")]
    monkeypatch.setattr(scenarios, "__path__", [*scenarios.__path__, *package_dirs("scenarios", private)])
    try:
        assert "globex_launch" in {m.name for m in pkgutil.iter_modules(scenarios.__path__)}
        assert importlib.import_module("scenarios.globex_launch").SLUG == "globex_launch"
    finally:
        sys.modules.pop("scenarios.globex_launch", None)
        if hasattr(scenarios, "globex_launch"):
            delattr(scenarios, "globex_launch")


# ---- trash ------------------------------------------------------------------

def test_delete_keeps_files_another_demo_uses(repo):
    demos = load_demos(repo)
    hero = next(d for d in demos if d.id == "acme-hero")
    plan = trash.plan_delete(hero, demos, repo)
    assert "scenarios/assets/acme_logo.png" not in plan.move
    assert plan.kept_shared == (("scenarios/assets/acme_logo.png", "Acme · Other"),)


def test_examples_cannot_be_deleted(repo):
    demos = load_demos(repo)
    with pytest.raises(ValueError, match="hide it"):
        trash.plan_delete(next(d for d in demos if d.example), demos, repo)


def test_delete_then_restore_round_trips(repo):
    demos = load_demos(repo)
    hero = next(d for d in demos if d.id == "acme-hero")
    before = {p: p.read_text() for p in owned_files(hero, repo)}
    state.set_hidden("acme-hero", ("acme_hero",), (), hidden=True)

    entry = trash.delete(trash.plan_delete(hero, demos, repo), repo)

    assert not (repo / "demos/acme-hero.toml").exists()
    assert not (repo / "docs/demos/acme-hero").exists()  # emptied folder pruned
    assert (repo / "scenarios/assets/acme_logo.png").exists()  # shared, kept
    assert (repo / "scenarios").is_dir()  # protected folder survives
    assert "acme-hero" not in state.hidden_demo_ids()  # delete clears hide
    assert [e.name for e in trash.list_trash(repo)] == [entry.name]

    trash.restore(entry.name, repo)
    assert {p: p.read_text() for p in owned_files(hero, repo)} == before
    assert trash.list_trash(repo) == []


def test_restore_refuses_to_overwrite_and_changes_nothing(repo):
    demos = load_demos(repo)
    hero = next(d for d in demos if d.id == "acme-hero")
    entry = trash.delete(trash.plan_delete(hero, demos, repo), repo)
    _write(repo, "scenarios/acme_hero.py", "SLUG = 'someone_elses'\n")

    with pytest.raises(trash.RestoreConflict) as err:
        trash.restore(entry.name, repo)
    assert err.value.paths == ["scenarios/acme_hero.py"]
    assert not (repo / "demos/acme-hero.toml").exists()
    assert (repo / "scenarios/acme_hero.py").read_text() == "SLUG = 'someone_elses'\n"
    assert trash.list_trash(repo)[0].name == entry.name


def test_empty_trash(repo):
    demos = load_demos(repo)
    hero = next(d for d in demos if d.id == "acme-hero")
    trash.delete(trash.plan_delete(hero, demos, repo), repo)
    assert trash.empty_trash(root=repo) == 1
    assert trash.list_trash(repo) == []


# ---- state ------------------------------------------------------------------

def test_hide_rebuilds_the_lists_the_routers_read():
    state.set_hidden("a", ("s1", "s2"), ("t1",), hidden=True)
    state.set_hidden("b", ("s3",), (), hidden=True)
    assert state.hidden_slugs("code") == {"s1", "s2", "s3"}
    assert state.hidden_slugs("tag") == {"t1"}
    state.set_hidden("a", (), (), hidden=False)
    assert state.hidden_slugs("code") == {"s3"} and state.hidden_slugs("tag") == set()
    assert state.hidden_demo_ids() == {"b"}


def test_missing_or_corrupt_state_means_nothing_hidden():
    assert state.hidden_slugs("code") == set()
    state.STATE_PATH.write_text("{not json")
    assert state.hidden_slugs("code") == set() and state.last_run("x", "y") is None


# ---- actions ----------------------------------------------------------------

def test_only_declared_flags_reach_the_command():
    a = Action(id="post", label="Post", run=("channels/seed.py", "--create"),
               options=(Option("--invite", "Invite"),))
    assert actions.build_command(a, ["--invite"])[1:] == ["channels/seed.py", "--create", "--invite"]
    with pytest.raises(ValueError, match="--channel"):
        actions.build_command(a, ["--channel", "C123"])


def test_run_action_streams_output_and_logs_the_run(repo):
    script = _write(repo, "channels/fake_seed.py", "import sys\nprint('one')\nprint('two')\nsys.exit(3)\n")
    hero = parse(repo / "demos" / "acme-hero.toml")
    a = Action(id="fake", label="Fake", run=(str(script),))
    lines = []
    assert actions.run_action(hero, a, on_line=lines.append) == 3
    assert lines == ["one", "two"]
    assert state.last_run("acme-hero", "fake")["exit_code"] == 3


def test_ago():
    import time
    assert actions.ago(time.time()) == "just now"
    assert actions.ago(time.time() - 3 * 86400) == "3 days ago"


# ---- routing preview (real scenarios package) ---------------------------------

def test_hiding_a_demo_takes_its_phrase_out_of_routing():
    demos = load_demos()
    phrase = "refactor the billing webhook to a durable queue"
    assert routing.preview(phrase, demos).scenario == "billing_webhook"
    state.set_hidden("billing-webhook", ("billing_webhook",), (), hidden=True)
    assert routing.preview(phrase, demos).scenario == "freeform"


def test_without_asks_what_a_phrase_would_open_if_a_demo_were_muted():
    demos = load_demos()
    phrase = "refactor the billing webhook to a durable queue"
    assert routing.preview(phrase, demos, without=("billing_webhook",)).scenario == "freeform"
    assert state.hidden_demo_ids() == set()  # asking doesn't mute anything
    assert routing.preview(phrase, demos).scenario == "billing_webhook"


def test_phrases_are_the_scenarios_own_keywords():
    import scenarios.billing_webhook as code
    import scenarios_tag.blog_update as tag
    demos = {d.id: d for d in load_demos()}
    assert routing.phrases(demos["billing-webhook"]) == tuple(code.ROUTE_KEYWORDS)
    assert routing.phrases(demos["blog-update"]) == tuple(tag.KEYWORDS)
    assert routing.phrases(demos["freeform-build"]) == ()   # the fallback claims no phrase


def test_a_demo_with_no_bot_story_has_no_phrases(repo):
    _write(repo, "demos/today-view.toml", """\
        name = "Today view"
        surfaces = ["slack"]
        [[actions]]
        id = "post"
        label = "Post"
        run = ["channels/seed_acme_hero.py"]
        """)
    demos = {d.id: d for d in load_demos(repo)}
    assert routing.phrases(demos["today-view"]) == ()


def test_task_words_are_real_intent_keywords():
    from agent.slackcode import load_slack_code_config
    words = routing.task_words()
    assert words and set(words) <= set(load_slack_code_config().intent_keywords)


def test_preview_flag_parses(tmp_path):
    p = _write(tmp_path, "demos/x.toml", """\
        name = "X"
        [[actions]]
        id = "dry"
        label = "Preview"
        run = ["s.py", "--dry-run"]
        preview = true
        [[actions]]
        id = "real"
        label = "Post"
        run = ["s.py"]
        """)
    d = parse(p)
    assert not d.problems
    assert d.action("dry").preview and not d.action("real").preview


def test_plain_question_is_a_reply_not_a_channel():
    assert routing.preview("what's the latest on the launch?", load_demos()).surface == "reply"


def test_cli_run_refuses_destructive_without_yes(repo, monkeypatch):
    from demokit import cli
    _write(repo, "demos/wipe.toml", """\
        name = "Wipe"
        [[actions]]
        id = "clear"
        label = "Clear messages"
        run = ["channels/seed_acme_hero.py", "--delete-only"]
        destructive = true
        """)
    monkeypatch.setattr(cli, "load_demos", lambda: load_demos(repo))
    with pytest.raises(SystemExit, match="--yes"):
        cli.main(["run", "wipe", "clear"])


def test_cli_rejects_stray_args_outside_run():
    from demokit import cli
    with pytest.raises(SystemExit):
        cli.main(["list", "--bogus"])


def test_python_is_the_venv_interpreter():
    a = Action(id="x", label="X", run=("s.py",))
    assert actions.build_command(a)[0] == sys.executable
