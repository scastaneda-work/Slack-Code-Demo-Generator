"""The demo manager's main window.

A block-letter banner (hidden when the window is too short for it), then the
bot: one bot serves every demo, so its status, Start/Stop
and settings live there, never inside a demo. Below, a sidebar of demos (yours
first; the built-in samples tucked into a collapsed section) and, for the one
you pick, its story, a short numbered checklist (set the scene, start it in
Slack) and the phrases it answers to. Everything else — testing a
phrase, checks, Recently Deleted, the raw activity log — lives behind More (M).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalGroup, Vertical, VerticalScroll
from textual.events import Resize
from textual.geometry import Size
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Button, OptionList, RichLog, Rule, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from demokit import actions, health, personas, routing, slack_links, state, trash
from demokit.bot import BotProcess, other_bot_pids
from demokit.manifest import ROOT, Action, Demo, load_demos
from demokit.tui import palette as pal
from demokit.tui.banner import Banner
from demokit.tui.screens import (
    MUTED, BotSettingsScreen, CheckScreen, ConfirmScreen, DeleteScreen, DocScreen, MoreScreen, OutputScreen,
    RecentlyDeletedScreen, RouteScreen, Tick, Toggle, link, open_external,
)

SAMPLES = "__samples__"
SIDEBAR_TEXT = 24  # characters a sidebar row has room for (see #sidebar in app.tcss)
BANNER_MIN_HEIGHT = 34  # rows the window needs before the big banner shows
WIDE = 110              # columns before the top bar and hints use their long wording
GOOD, ACCENT, DANGER, WARN = pal.GOOD, pal.ACCENT, pal.DANGER, pal.WARN  # what each colour means: see palette.py
SURFACE_NAMES = {"slack_code": "Slack Code", "claude_tag": "Claude Tag", "slack": "Seeded messages"}

SYNTHWAVE = Theme(
    name="synthwave", dark=True,
    primary=pal.ACCENT, secondary=pal.VIOLET, accent=pal.CYAN,
    success=pal.GOOD, warning=pal.WARN, error=pal.DANGER,
    foreground=pal.TEXT, background=pal.BG, surface=pal.SURFACE, panel=pal.PANEL,
    variables=pal.CSS_VARIABLES,
)


class DemoList(OptionList):
    """The sidebar. Return runs the selected demo's next step (a click only selects)."""

    BINDINGS = [Binding("enter", "app.next_step", "Next step", show=False)]


def section(label: str) -> Static:
    return Static(pal.fade().append(f" {label.upper()}", style=f"bold {pal.SECTION}"), classes="section")


def tagline(words: str) -> Text:
    return Text.assemble(("> ", f"bold {pal.CYAN}"), (words, pal.CYAN), ("▌", pal.PINK))


TAGLINE, SHORT_TAGLINE = tagline("build, stage and run Slack Code demos"), tagline("Slack Code demos")
COMPACT_TITLE = pal.gradient("▓▒░ SLACK CODE DEMOS ░▒▓", style="bold")


class DemoApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "Demos"
    ENABLE_COMMAND_PALETTE = False
    # Classes on the screen by window size: app.tcss hides the banner on -short.
    VERTICAL_BREAKPOINTS = [(0, "-short"), (BANNER_MIN_HEIGHT, "-tall")]
    BINDINGS = [
        Binding("n", "new_demo", show=False),
        Binding("b", "toggle_bot", show=False),
        Binding("m", "more", show=False),
        Binding("a", "agent", show=False),
        Binding("p", "route", show=False),
        Binding("t", "check", show=False),
        Binding("x", "recently_deleted", show=False),
        Binding("l", "activity", show=False),
        Binding("q", "quit", show=False),
    ]

    def __init__(self, root: Path = ROOT, bot: BotProcess | None = None):
        super().__init__()
        self.root = root
        self.bot = bot or BotProcess(cwd=root)
        self.demos: list[Demo] = []
        self.checks: dict[str, list[health.Check]] = {}
        self.selected: str | None = None      # a demo id, SAMPLES, or None
        self.samples_open = False
        self.running: tuple[str, str] | None = None   # (demo id, action id) while an action runs
        # What each action printed this session, and how it ended: (demo, action) → (lines, exit code).
        self.outputs: dict[tuple[str, str], tuple[list[str], int | None]] = {}
        self.bot_elsewhere = False  # a bot started outside this window (./run.sh in another Terminal)
        self._bot_was_running = False
        self._bot_stopping = False  # our own Stop/Restart — not a crash to report
        self._page_lock = asyncio.Lock()
        self._window: Size | None = None  # the latest size (App.size catches up a moment after a resize)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        # app.tcss is parsed before on_mount registers the theme; its $line, $muted, … come from here.
        return {**super().get_theme_variable_defaults(), **pal.CSS_VARIABLES}

    # ---- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Banner(id="banner")
        with Horizontal(id="topbar"):
            yield Static(TAGLINE, id="app-title")
            yield Static("", id="bot-status")
            yield Button("", id="bot-toggle", compact=True)
            yield Button("More…", id="more", compact=True, flat=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield DemoList(id="demos")
                yield link("＋ New demo", id="new-demo")
            with VerticalScroll(id="detail"):
                yield Vertical(id="page")
        with Vertical(id="activity"):
            with TabbedContent(id="activity-tabs"):
                with TabPane("Activity", id="tab-output"):
                    yield RichLog(id="output", wrap=True, markup=False)
                with TabPane("Bot log", id="tab-bot"):
                    yield RichLog(id="bot-log", wrap=True, markup=False)
        yield Static(id="hints")

    def on_mount(self) -> None:
        self.register_theme(SYNTHWAVE)
        self.theme = "synthwave"
        self.query_one("#sidebar").border_title = "DEMOS"
        self.query_one("#activity").display = False
        self._fit_banner()
        self.query_one("#output", RichLog).write(Text("What you do here (and what your demos' actions print) shows up here.",
                                                      style=MUTED))
        self.reload()
        self._update_chrome()
        self.set_interval(0.25, self._pump_bot)
        self._look_for_other_bot()
        self.set_interval(3, self._look_for_other_bot)
        self.query_one("#demos", DemoList).focus()
        if any(not c.ok and not c.warn for c in health.setup_checks(self.root)):
            self.push_screen(CheckScreen(self.root, self.demos, run_self_test=False))

    @property
    def window(self) -> Size:
        return self._window or self.size

    def on_resize(self, event: Resize) -> None:
        self._window = event.size
        self._fit_banner()
        self._update_chrome()

    def _fit_banner(self) -> None:
        """The tagline beside the big banner; a one-line title when it's hidden."""
        big = self.window.height >= BANNER_MIN_HEIGHT
        title = (TAGLINE if self.window.width >= WIDE else SHORT_TAGLINE) if big else COMPACT_TITLE
        self.query_one("#app-title", Static).update(title)

    # ---- the bot, as the top bar shows it -------------------------------------

    @property
    def agent(self) -> str:
        """The name the bot shows in Slack — what the SE types after @."""
        cur = personas.current(self.root)
        return (cur.slack_name if cur and cur.slack_name else "") or "the bot"

    @property
    def bot_ready(self) -> bool:
        return self.bot.running or self.bot_elsewhere

    def _update_chrome(self) -> None:
        wide = self.window.width >= WIDE
        status = Text("BOT ", style=f"bold {MUTED}")
        if self.bot.running:
            status.append("● RUNNING", style=f"bold {GOOD}")
        elif self.bot_elsewhere:
            status.append("● RUNNING IN ANOTHER TERMINAL" if wide else "● RUNNING ELSEWHERE", style=f"bold {GOOD}")
        else:
            status.append("○ OFF", style=f"bold {MUTED}")
        status.append("  ·  appears in Slack as " if wide else " · as ", style=MUTED)
        status.append(self.agent, style=pal.TEXT).append("   ")
        self.query_one("#bot-status", Static).update(status)
        toggle = self.query_one("#bot-toggle", Button)
        toggle.label = "Stop bot" if self.bot.running else "Start bot"
        toggle.display = not (self.bot_elsewhere and not self.bot.running)
        hints = Text()
        keys = (("↑↓", "Choose", "Choose"), ("⏎", "Next step", "Next"), ("B", "Start/stop bot", "Bot"),
                ("A", "Bot settings", "Settings"), ("N", "New demo", "New"), ("M", "More", "More"))
        for key, long, short in keys:
            hints.append(f" [{key}]", style=f"bold {ACCENT}").append(f" {long if wide else short}  ", style=MUTED)
        self.query_one("#hints", Static).update(hints)

    @work(thread=True, exclusive=True, group="elsewhere")
    def _look_for_other_bot(self) -> None:
        found = bool(other_bot_pids(self.bot))
        self.call_from_thread(self._other_bot_seen, found)

    def _other_bot_seen(self, found: bool) -> None:
        if found != self.bot_elsewhere:
            self.bot_elsewhere = found
            self._update_chrome()
            self.call_later(self._render_page)

    # ---- sidebar --------------------------------------------------------------

    def reload(self, select: str | None = None) -> None:
        self.demos = load_demos(self.root)
        if select is not None:
            self.selected = select
        self._fill_list()
        self._compute_health()

    def _fill_list(self) -> None:
        hidden = state.hidden_demo_ids()
        mine = [d for d in self.demos if not d.example]
        samples = [d for d in self.demos if d.example]
        if self.selected in {d.id for d in samples}:
            self.samples_open = True
        ol = self.query_one("#demos", DemoList)
        ol.clear_options()
        rows: list[str | None] = []

        def add(option: Option, row: str | None) -> None:
            ol.add_option(option)
            rows.append(row)

        def demo_row(d: Demo, indent: str = "") -> None:
            room = SIDEBAR_TEXT - len(indent) - (7 if d.id in hidden else 0)
            name = d.name if len(d.name) <= room else d.name[:room - 1].rstrip() + "…"
            label = Text(indent + name, style=MUTED if d.id in hidden else "")
            if d.id in hidden:
                label.append("  muted", style=f"italic {MUTED}")
            checks = self.checks.get(d.id) or []
            if any(not c.ok and not c.warn for c in checks):
                label.append("  ⚠", style=WARN)
            add(Option(label, id=d.id), d.id)

        # Your demos, one group per customer (as the manifest spells it), then the samples.
        add(Option(Text("Your demos", style="bold"), disabled=True), None)
        if not mine:
            add(Option(Text("  None yet", style=MUTED), disabled=True), None)
        groups: dict[str, list[Demo]] = {}
        for d in mine:
            groups.setdefault(d.customer, []).append(d)
        for i, (customer, ds) in enumerate(groups.items()):
            if i:
                add(Option(" ", disabled=True), None)
            add(Option(Text(f"  {customer or 'No customer'}", style=MUTED).append(f"  {len(ds)}", style=MUTED),
                       disabled=True), None)
            for d in ds:
                demo_row(d, indent="    ")
        if samples:
            add(Option(" ", disabled=True), None)
            arrow = "▾" if self.samples_open else "▸"
            add(Option(Text(f"{arrow} Sample demos").append(f"  {len(samples)}", style=MUTED), id=SAMPLES), SAMPLES)
            if self.samples_open:
                for d in samples:
                    demo_row(d, indent="  ")

        ids = [r for r in rows if r]
        if self.selected not in ids:
            self.selected = ids[0] if ids else None
        if self.selected is not None:
            ol.highlighted = rows.index(self.selected)
        self.call_later(self._render_page)

    @work(thread=True, exclusive=True, group="health")
    def _compute_health(self) -> None:
        demos = list(self.demos)
        result = {d.id: health.demo_checks(d, demos, self.root) for d in demos}
        self.call_from_thread(self._health_done, result)

    def _health_done(self, result: dict[str, list[health.Check]]) -> None:
        self.checks = result
        self._fill_list()

    @on(OptionList.OptionHighlighted, "#demos")
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_id and event.option_id != self.selected:
            self.selected = event.option_id
            self.call_later(self._render_page)

    @on(OptionList.OptionSelected, "#demos")
    def _clicked(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == SAMPLES:
            self._toggle_samples()

    def _toggle_samples(self) -> None:
        self.samples_open = not self.samples_open
        self.selected = SAMPLES
        self._fill_list()

    @property
    def current(self) -> Demo | None:
        return next((d for d in self.demos if d.id == self.selected), None)

    # ---- the page -------------------------------------------------------------

    async def _render_page(self) -> None:
        async with self._page_lock:  # an action finishing can re-render mid-render
            page = self.query_one("#page", Vertical)
            await page.remove_children()
            await page.mount_all(self._page_widgets())

    def _page_widgets(self) -> list[Widget]:
        if self.selected == SAMPLES:
            return [
                Static("Sample demos", classes="title"),
                Static("Ship with the tool", classes="subtitle"),
                Static("Ready-made stories: a homepage redesign, a webhook refactor, an incident, a flaky "
                       "test, a migration and more. Run one as-is, or use one as a reference when you build "
                       "your own.", classes="summary"),
                Static(Text("Press ⏎ to " + ("hide them." if self.samples_open else "show them."), style=MUTED)),
            ]
        d = self.current
        if d is None:
            return [
                Static("No demos yet", classes="title"),
                Static("Build your first one with Claude Code: it asks about your customer and story, "
                       "then writes the demo for you.", classes="summary"),
                Button("New demo", id="empty-new", compact=True, variant="primary"),
            ]
        hidden = d.id in state.hidden_demo_ids()
        kind = " · ".join(SURFACE_NAMES.get(s, s) for s in d.surfaces)
        out: list[Widget] = [
            Static(d.name, classes="title"),
            Static(" · ".join(x for x in ("Sample demo" if d.example else d.customer, kind) if x), classes="subtitle"),
            Static(d.summary, classes="summary"),
        ]
        if d.example:
            out.append(Static("Ships with the tool. Run it as-is, or use it as a reference when you build your own.",
                              classes="note"))
        out += self._warnings(d)

        steps = self._steps(d)
        if steps:
            out.append(section("Run the demo"))
            nxt = next((i for i, s in enumerate(steps) if not s["done"]), None)
            for i, s in enumerate(steps):
                out.append(self._step_widget(i + 1, s, primary=(i == nxt), hidden=hidden))

        if d.scenarios or d.tag_scenarios:
            out.append(section("Trigger phrases"))
            out += self._phrases(d, hidden)

        if d.docs:
            out.append(section("Docs"))
            out.append(HorizontalGroup(*[link(f"{doc.label} {'›' if doc.path.endswith('.md') else '↗'}",
                                              name=f"doc:{i}") for i, doc in enumerate(d.docs)],
                                       classes="controls"))

        if not d.example:
            out.append(Rule(classes="divider"))
            out.append(link("Delete demo…", name="delete", danger=True))
        return out

    def _warnings(self, d: Demo) -> list[Widget]:
        out = []
        errors = [c for c in self.checks.get(d.id, []) if not c.ok and not c.warn]
        if errors:
            more = f" and {len(errors) - 1} more" if len(errors) > 1 else ""
            out.append(Static(f"⚠  Something's missing: {errors[0].label}{more}. Press T to check everything.",
                              classes="warning"))
        f = state.bot_features()
        off = [name for name, slugs, key in (("Slack Code", d.scenarios, "slack_code"),
                                             ("In-thread tasks", d.tag_scenarios, "tag")) if slugs and not f[key]]
        if off:
            out.append(Static(f"⚠  {' and '.join(off)} {'is' if len(off) == 1 else 'are'} turned off for the bot, "
                              "so this demo won't start. Turn it on in Bot settings (A).", classes="warning"))
        return out

    def _steps(self, d: Demo) -> list[dict]:
        steps = []
        if d.actions:
            a = d.actions[0]
            last = state.last_run(d.id, a.id)
            steps.append({"kind": "scene", "title": a.step or "Set the scene", "action": a, "last": last,
                          "done": bool(last and last.get("exit_code") == 0)})
        if d.trigger and (d.scenarios or d.tag_scenarios):
            steps.append({"kind": "kick", "title": "Start it in Slack", "done": False})
        return steps

    def _step_widget(self, n: int, s: dict, primary: bool, hidden: bool) -> Widget:
        d = self.current
        running = s["kind"] == "scene" and self.running == (d.id, s["action"].id)
        head = Text()
        if s["done"] and not running:
            head.append("[OK]", style=f"bold {GOOD}")
        else:
            head.append(f"[{n:02}]", style=f"bold {ACCENT}" if primary or running else f"bold {MUTED}")
        head.append(f"  {s['title']}", style="bold")
        body = self._scene_body(d, s, primary, running) if s["kind"] == "scene" else self._kick_body(d, primary, hidden)
        return Vertical(Static(head, classes="step-head"), Vertical(*body, classes="step-body"), classes="step")

    def _scene_body(self, d: Demo, s: dict, primary: bool, running: bool) -> list[Widget]:
        a: Action = s["action"]
        body: list[Widget] = []
        if a.note:
            body.append(Static(a.note, classes="caption"))
        if a.options:
            body.append(Vertical(*[Tick(o.label, id=f"opt-{a.id}-{i}", compact=True, classes="option")
                                   for i, o in enumerate(a.options)], classes="options"))
        controls: list[Widget] = [Button(a.label, name=f"action:{a.id}", compact=True, disabled=running,
                                         variant="primary" if primary else "default")]
        controls += self._run_status(d, a, s["last"], running)
        body.append(HorizontalGroup(*controls, classes="controls"))
        if running:
            body.append(Static(Text("Working on it…", style=MUTED), id="live-line", classes="caption"))
        for x in d.actions[1:]:
            x_running = self.running == (d.id, x.id)
            row: list[Widget] = [link(x.label, name=f"action:{x.id}", danger=x.destructive)]
            if x.note:
                row.append(Static(x.note, classes="caption alt-note"))
            row += self._run_status(d, x, state.last_run(d.id, x.id), x_running, quiet=True)
            body.append(HorizontalGroup(*row, classes="alt-action"))
        return body

    def _run_status(self, d: Demo, a: Action, last: dict | None, running: bool, quiet: bool = False) -> list[Widget]:
        """'Done 2 min ago · Show output' next to an action's button."""
        if running:
            return [Static(Text("Running…", style=ACCENT), classes="status")] if quiet else []
        out: list[Widget] = []
        failed = bool(last) and last.get("exit_code") != 0
        if failed:
            out.append(Static(Text(f"Didn't finish ({actions.ago(last['started'])})", style=DANGER), classes="status"))
        elif last and not quiet:
            out.append(Static(Text(f"Done {actions.ago(last['started'])}", style=GOOD), classes="status"))
        if (d.id, a.id) in self.outputs and (failed or not quiet or a.preview):
            out.append(link("Show output", name=f"output:{a.id}"))
        return out

    def _kick_body(self, d: Demo, primary: bool, hidden: bool) -> list[Widget]:
        body: list[Widget] = []
        if hidden:
            body.append(Static(f"This demo is muted, so {self.agent} won't respond. "
                               "Turn on “Respond to these phrases” below.", classes="warning"))
        if d.actions:
            where = "Reply in the backstory thread" + (f" in #{d.channels[0]}" if d.channels else "") + " with"
        elif d.tag_scenarios and not d.scenarios:
            where = "@-mention the bot in a channel thread with"
        else:
            where = "@-mention the bot in any channel with"
        body.append(Static(where, classes="caption"))
        body.append(Static(Text(f"@{self.agent} {d.trigger}", style=f"bold {ACCENT}"), classes="trigger"))
        controls: list[Widget] = [Button("Copy", name="copy", compact=True,
                                         variant="primary" if primary and self.bot_ready else "default")]
        controls += [link(f"Open #{c} ↗", name=f"channel:{c}") for c in d.channels[:2]]
        body.append(HorizontalGroup(*controls, classes="controls"))

        line = Text()
        if self.bot_ready:
            line.append("✓ ", style=f"bold {GOOD}").append("The bot is running.")
            line.append(" It's shared by every demo." if self.bot.running else " It was started in another Terminal.",
                        style=MUTED)
            body.append(Static(line, classes="bot-line"))
        else:
            line.append("○ ", style=MUTED).append("Needs the bot, and it's off. ")
            line.append("Start bot", style=f"bold {ACCENT}").append(" is at the top (or press B). ")
            line.append("One bot serves every demo, so you only start it once.", style=MUTED)
            body.append(Static(line, classes="bot-line"))
        return body

    def _phrases(self, d: Demo, hidden: bool) -> list[Widget]:
        out: list[Widget] = [HorizontalGroup(Static("Respond to these phrases", classes="setting-label"),
                                             Toggle(not hidden, id="responds"), classes="setting")]
        if hidden:
            out.append(Static(f"Muted. {self.agent} ignores this demo's phrases until you turn this back on. "
                              "Nothing is deleted.", classes="caption"))
        phrases = routing.phrases(d)
        if not phrases:
            out.append(Static("Answers any build request that no other demo claims, like the one above.",
                              classes="caption"))
        else:
            if d.scenarios:
                words = ", ".join(routing.task_words())
                out.append(Static(f"Besides the phrase above, any @-mention that has one of these and a task word "
                                  f"({words}…) opens this demo:", classes="caption"))
            else:
                out.append(Static("Besides the phrase above, any @-mention that has one of these opens this demo:",
                                  classes="caption"))
            # Break lines between phrases ourselves, so none is split across two lines.
            chips, used = Text(), 0
            for p in phrases:
                chip = f"“{p}”"
                if used and used + 3 + len(chip) > 80:
                    chips.append("\n")
                    used = 0
                elif used:
                    chips.append("   ")
                    used += 3
                chips.append(chip, style=pal.PHRASE)
                used += len(chip)
            out.append(Static(chips, classes="phrases"))

        if d.trigger:
            r = routing.preview(d.trigger, self.demos, without=() if hidden else d.scenarios + d.tag_scenarios)
            if r.surface == "reply":
                then = "just gets a reply in the thread"
            elif r.scenario == "freeform" or r.demo is None:
                then = "builds something from scratch"
            else:
                then = f"opens “{r.demo.name}”"
            out.append(Static(f"{'While muted' if hidden else 'If you mute it'}, the phrase above {then} instead. "
                              "Press P to try other wording.", classes="caption"))
        return out

    # ---- buttons and keys -----------------------------------------------------

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        name = event.button.name or ""
        d = self.current
        kind, _, arg = name.partition(":")
        if kind == "action" and d:
            a = d.action(arg)
            if a:
                self._start_action(d, a)
        elif kind == "output" and d:
            self._show_output(d, arg)
        elif kind == "doc" and d:
            self._open_doc(d, int(arg))
        elif kind == "channel":
            self._open_channel(arg)
        elif name == "copy":
            self._copy_trigger()
        elif name == "delete":
            self.action_delete()

    @on(Button.Pressed, "#bot-toggle")
    def _bot_toggle(self) -> None:
        self.action_toggle_bot()

    @on(Button.Pressed, "#more")
    def _more_pressed(self) -> None:
        self.action_more()

    @on(Button.Pressed, "#new-demo")
    @on(Button.Pressed, "#empty-new")
    def _new_pressed(self) -> None:
        self.action_new_demo()

    @on(Toggle.Changed, "#responds")
    def _responds(self, event: Toggle.Changed) -> None:
        d = self.current
        if d is None or (d.id not in state.hidden_demo_ids()) == event.value:
            return
        state.set_hidden(d.id, d.scenarios, d.tag_scenarios, hidden=not event.value)
        self.record(f"Turned “{d.name}” {'on' if event.value else 'off'}")
        self.notify(f"{self.agent} responds to “{d.name}” again." if event.value
                    else f"Muted “{d.name}”. {self.agent} ignores its phrases until you turn it back on.")
        self._fill_list()

    def action_next_step(self) -> None:
        if self.selected == SAMPLES:
            self._toggle_samples()
            return
        d = self.current
        if d is None:
            self.action_new_demo()
            return
        s = next((s for s in self._steps(d) if not s["done"]), None)
        if s is None:
            self.notify("All set.")
        elif s["kind"] == "scene":
            self._start_action(d, s["action"])
        elif not self.bot_ready:
            self._start_bot()
        else:
            self._copy_trigger()

    def _copy_trigger(self) -> None:
        d = self.current
        if not (d and d.trigger):
            return
        if shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=d.trigger, text=True)
        else:
            self.copy_to_clipboard(d.trigger)
        self.notify(f"Copied. In Slack, type @{self.agent}, pick it, then paste.")

    def _open_doc(self, d: Demo, i: int) -> None:
        """Markdown reads here in the window; anything else (an HTML click path) opens in its app."""
        doc = d.docs[i]
        path = self.root / doc.path
        if not path.is_file():
            self.notify(f"Can't find {doc.path}.", severity="error")
        elif path.suffix.lower() in (".md", ".markdown"):
            self.push_screen(DocScreen(f"{doc.label}: {d.name}", path))
        else:
            open_external(str(path))

    @work(thread=True, group="channel")
    def _open_channel(self, name: str) -> None:
        try:
            url = slack_links.channel_url(name)
        except Exception as e:  # noqa: BLE001 — tell the SE, keep the window up
            self.call_from_thread(self.notify, f"Couldn't look up #{name}: {e}", severity="error")
            return
        if url:
            open_external(url)
        else:
            self.call_from_thread(self.notify, f"There's no #{name} in Slack yet. Set the scene first.",
                                  severity="warning")

    # ---- actions --------------------------------------------------------------

    def _flags(self, a: Action) -> list[str]:
        flags = []
        for i, o in enumerate(a.options):
            boxes = self.query(f"#opt-{a.id}-{i}")
            if boxes and boxes.first(Tick).value:
                flags.append(o.flag)
        return flags

    def _start_action(self, d: Demo, a: Action) -> None:
        if self.running:
            self.notify("Hang on — the last action is still running.", severity="warning")
            return
        flags = self._flags(a)
        last = state.last_run(d.id, a.id)
        if a.destructive or a.confirm:
            ask = ConfirmScreen(f"{a.label.rstrip('…')}?", a.confirm or "This changes things in Slack.",
                                a.label.rstrip("…"), danger=a.destructive)
        elif last and last.get("exit_code") == 0 and a.repeat_warning:
            ask = ConfirmScreen(f"{a.label} again?", f"Last done {actions.ago(last['started'])}. {a.repeat_warning}",
                                f"{a.label} again")
        else:
            self._run_action(d, a, flags)
            return
        self.push_screen(ask, lambda ok: ok and self._run_action(d, a, flags))

    def _output_sheet(self, d: Demo, a: Action, lines: list[str], code: int | None, live: bool) -> OutputScreen:
        note = a.note or ("Nothing in Slack changes." if a.preview else "")
        return OutputScreen(f"{a.label}: {d.name}", note, lines, code=code, live=live)

    def _show_output(self, d: Demo, action_id: str) -> None:
        a = d.action(action_id)
        lines, code = self.outputs.get((d.id, action_id), ([], None))
        if a:
            self.push_screen(self._output_sheet(d, a, lines, code, live=False))

    @work(exclusive=True, group="action")
    async def _run_action(self, d: Demo, a: Action, flags: list[str]) -> None:
        self.running = (d.id, a.id)
        lines: list[str] = []
        self.outputs[(d.id, a.id)] = (lines, None)
        # A preview's whole point is what it prints, so it opens straight into a window.
        sheet = self._output_sheet(d, a, [], None, live=True) if a.preview else None
        if sheet:
            self.push_screen(sheet)
        await self._render_page()
        log = self.query_one("#output", RichLog)
        self.record(f"▶ {d.name}: {a.label}{' ' + ' '.join(flags) if flags else ''}", style="bold")

        def on_line(line: str) -> None:
            lines.append(line)
            log.write(Text.from_ansi(line))
            if sheet:
                sheet.add(line)
            live = self.query("#live-line")
            if live and line.strip():
                live.first(Static).update(Text(Text.from_ansi(line).plain[:90], style=MUTED))

        try:
            code = await actions.run_action_async(d, a, flags, on_line=on_line)
        except Exception as e:  # noqa: BLE001 — report it, keep the window up
            on_line(f"Couldn't start it: {e}")
            code = -1
        finally:
            self.running = None
        self.outputs[(d.id, a.id)] = (lines, code)
        if sheet:
            sheet.finish(code)
        self.record(f"{'✓ Done' if code == 0 else f'✗ Failed (exit {code})'}: {a.label}",
                    style=GOOD if code == 0 else DANGER)
        if code != 0 and not sheet:
            self.notify(f"“{a.label}” didn't finish. Click Show output to see why.", severity="error")
        await self._render_page()

    def action_delete(self) -> None:
        d = self.current
        if d is None or d.example:
            return
        plan = trash.plan_delete(d, self.demos, self.root)

        def confirmed(ok):
            if not ok:
                return
            trash.delete(plan, self.root)
            self.record(f"Deleted “{d.name}” (restorable from Recently Deleted)")
            msg = f"Deleted “{d.name}”. Restore it from Recently Deleted (X)."
            if self.bot.running:
                msg += " Restart the bot so it lets go of this demo."
            self.notify(msg, timeout=8)
            self.selected = None
            self.reload()

        self.push_screen(DeleteScreen(plan), confirmed)

    # ---- More and its sheets --------------------------------------------------

    def action_more(self) -> None:
        def chosen(action):
            if action:
                getattr(self, f"action_{action}")()
        self.push_screen(MoreScreen(), chosen)

    def action_route(self) -> None:
        self.push_screen(RouteScreen(self.demos, self.agent))

    def action_check(self) -> None:
        self.push_screen(CheckScreen(self.root, self.demos))

    def action_recently_deleted(self) -> None:
        self.push_screen(RecentlyDeletedScreen(self.root), lambda changed: changed and self.reload())

    def action_activity(self, show: bool | None = None) -> None:
        drawer = self.query_one("#activity")
        drawer.display = (not drawer.display) if show is None else show

    def action_agent(self) -> None:
        self.push_screen(BotSettingsScreen(self.root, state.bot_features(), self.bot.running), self._agent_chosen)

    def _agent_chosen(self, result: dict | None) -> None:
        if not result:
            return
        before = personas.current(self.root)
        changed = False
        if result["persona"]:
            target = personas.get(result["persona"], self.root)
            if before is None or target is None or not before.matches(target):
                try:
                    p = personas.apply(result["persona"], self.root)
                except (KeyError, RuntimeError, OSError) as e:
                    self.notify(f"Couldn't switch: {e}", severity="error")
                    return
                changed = True
                self.record(f"Switched the bot's persona to {p.slack_name}")
                self.notify(f"The bot now appears as {p.slack_name}. Upload its picture in your app "
                            "settings (Bot settings → Open app settings).", timeout=8)
        f = state.bot_features()
        if (f["slack_code"], f["tag"]) != (result["slack_code"], result["tag"]):
            state.set_bot_features(result["slack_code"], result["tag"])
            self.record(f"Slack Code {'on' if result['slack_code'] else 'off'}, "
                        f"Claude Tag {'on' if result['tag'] else 'off'}")
            changed = True
        self._update_chrome()
        if changed and self.bot.running:
            self._restart_bot()
        else:
            self.call_later(self._render_page)

    def action_new_demo(self) -> None:
        claude = shutil.which("claude")
        if not claude:
            self.notify("Claude Code isn't installed, so it can't build a demo here.", severity="error")
            return
        before = {d.id for d in self.demos}
        with self.suspend():
            print("\nOpening Claude Code to build a new demo. Type /exit when you're done to come back.\n")
            subprocess.run([claude, "/build-slack-demo"], cwd=self.root)
        self.demos = load_demos(self.root)
        new = [d.id for d in self.demos if d.id not in before]
        self.reload(select=new[0] if new else None)
        if new:
            self.record(f"Built a new demo: {next(d.name for d in self.demos if d.id == new[0])}")
            self.notify("Your new demo is ready.")

    # ---- the bot --------------------------------------------------------------

    def _bot_env(self) -> dict[str, str]:
        f = state.bot_features()
        return {"SLACK_CODE_ENABLED": "1" if f["slack_code"] else "", "SLACK_TAG_ENABLED": "1" if f["tag"] else "0"}

    def record(self, msg: str, style: str = "") -> None:
        """Note something you did (or that happened) in the Activity tab."""
        line = Text(f"{datetime.now():%H:%M:%S}  ", style=MUTED)
        line.append(msg, style=style)
        self.query_one("#output", RichLog).write(line)

    def _bot_log(self, text: Text) -> None:
        self.query_one("#bot-log", RichLog).write(text)

    def _pump_bot(self) -> None:
        for line in self.bot.drain():
            self._bot_log(Text.from_ansi(line))
        running = self.bot.running
        if running != self._bot_was_running:
            code = self.bot.exit_code
            if not running and not self._bot_stopping and code not in (0, None, -2, 130):
                self.notify("The bot stopped unexpectedly. Press L and open Bot log to see why.",
                            severity="error", timeout=10)
            self._bot_was_running = running
            if running:
                self.record("Bot started", style=GOOD)
            elif self._bot_stopping or code in (0, None, -2, 130):
                self.record("Bot stopped")
            else:
                self.record(f"Bot stopped unexpectedly (exit {code}). See Bot log.", style=DANGER)
            self._update_chrome()
            self.call_later(self._render_page)

    def action_toggle_bot(self) -> None:
        if self.bot.running:
            self._stop_bot()
        else:
            self._start_bot()

    def _start_bot(self) -> None:
        if self.bot.running:
            return
        if other_bot_pids(self.bot):
            self.push_screen(ConfirmScreen(
                "The bot is already running somewhere else",
                "It looks like the bot was started in another Terminal (./run.sh). Two copies split "
                "Slack's messages between them, so the demo misbehaves. Stop the other one first.",
                "Start anyway"), lambda ok: ok and self._really_start_bot())
        else:
            self._really_start_bot()

    def _really_start_bot(self) -> None:
        self._bot_log(Text(f"▶ Starting the bot (appears in Slack as {self.agent})", style="bold"))
        try:
            self.bot.start(self._bot_env())
        except OSError as e:
            self._bot_log(Text(f"Couldn't start the bot: {e}", style=DANGER))
            self.notify(f"Couldn't start the bot: {e}", severity="error")
        self._pump_bot()

    @work(thread=True, exclusive=True, group="bot")
    def _stop_bot(self) -> None:
        self._bot_stopping = True
        self.call_from_thread(self._bot_log, Text("■ Stopping the bot…", style="bold"))
        self.bot.stop()
        self._bot_stopping = False
        self.call_from_thread(self._pump_bot)

    @work(thread=True, exclusive=True, group="bot")
    def _restart_bot(self) -> None:
        self._bot_stopping = True
        self.call_from_thread(self._bot_log, Text("↻ Restarting the bot…", style="bold"))
        self.bot.stop()
        self._bot_stopping = False
        self.call_from_thread(self._really_start_bot)

    async def action_quit(self) -> None:
        if not self.bot.running:
            self.exit()
            return
        self.push_screen(ConfirmScreen("The bot is running", "Quitting stops it, so no demo responds in Slack "
                                       "until you start it again.", "Stop bot and quit", danger=True),
                         lambda ok: ok and self._stop_then_exit())

    @work(thread=True)
    def _stop_then_exit(self) -> None:
        self._bot_stopping = True
        self.bot.stop()
        self.call_from_thread(self.exit)


def run() -> int:
    os.chdir(ROOT)
    DemoApp().run()
    return 0
