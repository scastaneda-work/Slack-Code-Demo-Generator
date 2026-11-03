"""The demo manager's sheets (pop-up dialogs). Each one dismisses with its
answer (``None`` = cancelled) so the main screen decides what to do with it.
Buttons follow the macOS order: Cancel on the left, the action on the right."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.containers import HorizontalGroup, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, Markdown, OptionList, RadioButton, RadioSet, RichLog, Static
from textual.widgets.option_list import Option

from demokit import actions, health, personas, routing, trash
from demokit.manifest import Demo
from demokit.tui import palette as pal

MUTED = pal.MUTED
GOOD, ACCENT, DANGER = pal.GOOD, pal.ACCENT, pal.DANGER  # what each colour means: see palette.py


class Toggle(Widget, can_focus=True):
    """An on/off switch drawn as text — ``━━● On`` / ``●━━ Off`` — that says what
    state it's in (Textual's slider doesn't, in a terminal)."""

    DEFAULT_CSS = "Toggle { width: auto; height: 1; }"
    BINDINGS = [Binding("space,enter", "toggle", show=False)]
    value: reactive[bool] = reactive(False, init=False)

    class Changed(Message):
        def __init__(self, toggle: Toggle, value: bool):
            super().__init__()
            self.toggle, self.value = toggle, value

        @property
        def control(self) -> Toggle:
            return self.toggle

    def __init__(self, value: bool = False, *, id: str | None = None):
        super().__init__(id=id)
        self.set_reactive(Toggle.value, value)

    def render(self) -> Text:
        if self.value:
            return Text("━━●", style=GOOD).append("  ON", style=f"bold {GOOD}")
        return Text("●━━", style=MUTED).append("  OFF", style=f"bold {MUTED}")

    def action_toggle(self) -> None:
        self.value = not self.value

    def on_click(self) -> None:
        self.value = not self.value

    def watch_value(self, value: bool) -> None:
        self.post_message(self.Changed(self, value))


class Tick(Checkbox):
    """A checkbox drawn as ``[ ]`` / ``[✓]`` (Textual's default shows a dim X
    either way, and stacked ones blur into one grey block)."""

    @property
    def _button(self) -> Content:
        style = self.get_visual_style("toggle--button")
        return Content.assemble(("[", style), (self.BUTTON_INNER, style), ("]", style))

    @property
    def BUTTON_INNER(self) -> str:  # noqa: N802 — overrides Textual's class constant
        return "✓" if self.value else " "


class Dot(RadioButton):
    """A radio button that's empty when off."""

    @property
    def BUTTON_INNER(self) -> str:  # noqa: N802
        return "●" if self.value else " "


def link(label: str, *, name: str | None = None, id: str | None = None, danger: bool = False) -> Button:
    """A text-only button, like a link in a macOS sheet."""
    return Button(label, name=name, id=id, compact=True, flat=True, classes="link danger" if danger else "link")


def open_external(target: str) -> None:
    cmd = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([cmd, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Sheet(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def action_cancel(self) -> None:
        self.dismiss(None)

    @staticmethod
    def buttons(*items: Button) -> HorizontalGroup:
        return HorizontalGroup(*items, classes="sheet-buttons")


class ConfirmScreen(Sheet):
    """Yes/no. Dismisses True, or None when cancelled."""

    def __init__(self, title: str, body: str, yes: str = "OK", danger: bool = False):
        super().__init__()
        self._title, self._body, self._yes, self._danger = title, body, yes, danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet"):
            yield Static(self._title, classes="sheet-title")
            yield Static(self._body, classes="sheet-body")
            yield self.buttons(
                Button("Cancel", id="no", compact=True),
                Button(self._yes, id="yes", compact=True, variant="error" if self._danger else "primary"),
            )

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(None)


class DeleteScreen(Sheet):
    """Dismisses True to move the demo to Recently Deleted."""

    def __init__(self, plan: trash.DeletePlan):
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        n, kept = len(self.plan.move), len(self.plan.kept_shared)
        body = (f"Its {n} file{'s' if n != 1 else ''} move to Recently Deleted, where you can restore them. "
                "Nothing in Slack changes.")
        if kept:
            body += f" {kept} file{'s' if kept != 1 else ''} another demo uses stay where they are."
        with Vertical(classes="sheet"):
            yield Static(f"Delete “{self.plan.demo.name}”?", classes="sheet-title")
            yield Static(body, classes="sheet-body")
            yield self.buttons(Button("Cancel", id="no", compact=True),
                               Button("Delete", id="yes", compact=True, variant="error"))

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(None)


class RecentlyDeletedScreen(Sheet):
    """Deleted demos: restore one, or remove it for good. Dismisses True if
    anything was restored (so the list reloads)."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.changed = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet wide"):
            yield Static("Recently Deleted", classes="sheet-title")
            yield OptionList(id="trash-list")
            yield self.buttons(
                Button("Done", id="close", compact=True),
                link("Delete Now", id="forever", danger=True),
                Button("Restore", id="restore", compact=True, variant="primary"),
            )

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        ol = self.query_one("#trash-list", OptionList)
        ol.clear_options()
        entries = trash.list_trash(self.root)
        if not entries:
            ol.add_option(Option(Text("Nothing here. Deleted demos wait here until you restore them.",
                                      style=MUTED), disabled=True))
        for e in entries:
            label = Text(e.demo_name).append(f"   deleted {actions.ago(e.deleted_at)}", style=MUTED)
            ol.add_option(Option(label, id=e.name))
        if entries:
            ol.highlighted = 0
        self.query_one("#restore", Button).disabled = not entries
        self.query_one("#forever", Button).display = bool(entries)

    def _selected(self) -> str | None:
        opt = self.query_one("#trash-list", OptionList).highlighted_option
        return opt.id if opt and not opt.disabled else None

    @on(Button.Pressed, "#restore")
    def _restore(self) -> None:
        name = self._selected()
        if not name:
            return
        try:
            e = trash.restore(name, self.root)
        except trash.RestoreConflict as c:
            self.notify("Couldn't restore it — these files exist again:\n" + "\n".join(c.paths),
                        severity="error", timeout=10)
            return
        self.changed = True
        self.app.record(f"Restored “{e.demo_name}”")
        self.notify(f"Restored “{e.demo_name}”.")
        self._refresh()

    @on(Button.Pressed, "#forever")
    def _forever(self) -> None:
        name = self._selected()
        if not name:
            return

        def done(ok):
            if ok:
                label = next((e.demo_name for e in trash.list_trash(self.root) if e.name == name), name)
                trash.empty_trash(name, self.root)
                self.app.record(f"Deleted “{label}” for good")
                self._refresh()

        self.app.push_screen(ConfirmScreen("Delete it for good?", "You can't undo this.", "Delete", danger=True), done)

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(self.changed)

    def action_cancel(self) -> None:
        self.dismiss(self.changed)


class OutputScreen(Sheet):
    """What an action printed, here in the terminal. ``live`` while it's still
    running: the app feeds it lines with add() and calls finish() at the end."""

    def __init__(self, title: str, note: str, lines: list[str], code: int | None = None, live: bool = False):
        super().__init__()
        self._title, self._note = title, note
        self.lines, self.code, self.live = list(lines), code, live

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet wide"):
            yield Static(self._title, classes="sheet-title")
            if self._note:
                yield Static(self._note, classes="sheet-note-top")
            yield RichLog(id="output-log", wrap=True, markup=False, classes="output-log")
            yield Static("", id="output-status")
            yield self.buttons(Button("Done", id="close", compact=True, variant="primary"))

    def on_mount(self) -> None:
        log = self.query_one("#output-log", RichLog)
        for line in self.lines:
            log.write(Text.from_ansi(line))
        self._status()

    def add(self, line: str) -> None:
        self.lines.append(line)
        if self.is_mounted:
            self.query_one("#output-log", RichLog).write(Text.from_ansi(line))

    def finish(self, code: int) -> None:
        self.live, self.code = False, code
        if self.is_mounted:
            self._status()

    def _status(self) -> None:
        if self.live:
            t = Text("Running…", style=ACCENT)
        elif self.code == 0:
            t = Text("✓ Finished", style=GOOD)
        elif self.code is None:
            t = Text("")
        else:
            t = Text(f"✗ Didn't finish (exit {self.code}). The lines above say why.", style=DANGER)
        if not self.lines and not self.live:
            t = Text("It didn't print anything.", style=MUTED).append("  ").append_text(t)
        self.query_one("#output-status", Static).update(t)

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


class DocScreen(Sheet):
    """A Markdown doc (a talk track, say), read here in the terminal."""

    def __init__(self, title: str, path: Path):
        super().__init__()
        self._title, self.path = title, path

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet doc"):
            yield Static(self._title, classes="sheet-title")
            with VerticalScroll(classes="doc-scroll"):
                yield Markdown(self.path.read_text(encoding="utf-8"))
            yield self.buttons(link("Open in another app ↗", id="open-doc"),
                               Button("Done", id="close", compact=True, variant="primary"))

    @on(Button.Pressed, "#open-doc")
    def _open(self) -> None:
        open_external(str(self.path))

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


class RouteScreen(Sheet):
    """Type a phrase, see which demo it would open, live."""

    def __init__(self, demos: list[Demo], agent: str):
        super().__init__()
        self.demos, self.agent = demos, agent

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet wide"):
            yield Static("Test a phrase", classes="sheet-title")
            yield Static(f"What happens when someone @-mentions {self.agent} with…", classes="sheet-body")
            yield Input(placeholder="implement the homepage redesign", id="phrase")
            yield Static("", id="route-result", classes="sheet-body")
            yield self.buttons(Button("Done", id="close", compact=True, variant="primary"))

    @on(Input.Changed, "#phrase")
    def _changed(self, event: Input.Changed) -> None:
        out = self.query_one("#route-result", Static)
        if not event.value.strip():
            out.update("")
            return
        out.update(routing.preview(event.value, self.demos).explanation)

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


class MoreScreen(Sheet):
    """Everything that isn't part of running a demo. Dismisses with the app
    action to run."""

    ITEMS = [
        ("route", "Test a phrase…", "P"),
        ("check", "Check everything", "T"),
        ("recently_deleted", "Recently Deleted", "X"),
        ("agent", "Bot settings…", "A"),
        ("activity", "Activity log", "L"),
    ]

    def compose(self) -> ComposeResult:
        options = [Option(Text(f"{label:<30}").append(key, style=MUTED), id=action)
                   for action, label, key in self.ITEMS]
        with Vertical(classes="sheet menu"):
            yield Static("More", classes="sheet-title")
            yield OptionList(*options, id="more-list")

    @on(OptionList.OptionSelected, "#more-list")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)


class CheckScreen(Sheet):
    """This machine's setup, every demo's files, and the offline self-test."""

    def __init__(self, root: Path, demos: list[Demo], run_self_test: bool = True):
        super().__init__()
        self.root, self.demos, self.run_self_test = root, demos, run_self_test

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet wide"):
            yield Static("Check everything", classes="sheet-title")
            with VerticalScroll(classes="sheet-scroll"):
                yield Static(self._setup_text(), id="setup")
                yield Static(self._demo_text(), id="demos-check")
                yield Static("", id="self-test")
            yield self.buttons(link("Run the self-test", id="run-test"),
                               Button("Done", id="close", compact=True, variant="primary"))

    def on_mount(self) -> None:
        self.query_one("#run-test", Button).display = not self.run_self_test
        if self.run_self_test:
            self._self_test()

    def _setup_text(self) -> Text:
        t = Text("This Mac\n", style="bold")
        for c in health.setup_checks(self.root):
            self._line(t, c)
        return t

    def _demo_text(self) -> Text:
        t = Text("\nDemos\n", style="bold")
        bad = [c for c in health.repo_checks(self.demos, self.root) if not c.ok]
        for d in self.demos:
            bad += [health.Check(f"{d.name}: {c.label}", False, c.detail, c.warn)
                    for c in health.demo_checks(d, self.demos, self.root) if not c.ok]
        if not bad:
            t.append("✓ ", style="green").append(f"All {len(self.demos)} demos have everything they need\n")
        for c in bad:
            self._line(t, c)
        return t

    @staticmethod
    def _line(t: Text, c: health.Check) -> None:
        mark, style = ("✓ ", "green") if c.ok else (("! ", "yellow") if c.warn else ("✗ ", "red"))
        t.append(mark, style=style).append(c.label + "\n")
        if not c.ok and c.detail:
            t.append(f"  {c.detail}\n", style=MUTED)

    @on(Button.Pressed, "#run-test")
    def _run_pressed(self) -> None:
        self.query_one("#run-test", Button).display = False
        self._self_test()

    @work(thread=True, exclusive=True)
    def _self_test(self) -> None:
        out = self.query_one("#self-test", Static)
        self.app.call_from_thread(out.update, Text("\nSelf-test\n", style="bold").append(
            "Running… (about 30 seconds)", style=MUTED))
        r = actions.self_test()
        t = Text("\nSelf-test\n", style="bold")
        if r.exit_code == 0:
            t.append("✓ ", style="green").append(f"All {r.passed} checks passed")
        else:
            t.append("✗ ", style="red").append(f"{r.failed} of {r.passed + r.failed} checks failed\n")
            for line in r.problems[:12]:
                t.append(f"  {line}\n", style=MUTED)
        self.app.call_from_thread(out.update, t)

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


class NewPersonaScreen(Sheet):
    """Name + maker → a new persona file. Dismisses with its slug."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = root

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet"):
            yield Static("New persona", classes="sheet-title")
            yield Static("The agent your audience sees in Slack. It talks like the Claude persona, "
                         "under its own name.", classes="sheet-body")
            yield Static("Name", classes="field-label")
            yield Input(placeholder="GitHub Copilot", id="p-name")
            yield Static("Made by", classes="field-label")
            yield Input(placeholder="GitHub", id="p-maker")
            yield Static("", id="p-error", classes="sheet-error")
            yield self.buttons(Button("Cancel", id="no", compact=True),
                               Button("Create", id="yes", compact=True, variant="primary"))

    @on(Input.Submitted)
    @on(Button.Pressed, "#yes")
    def _create(self) -> None:
        name = self.query_one("#p-name", Input).value
        maker = self.query_one("#p-maker", Input).value
        try:
            p = personas.create(name, maker, self.root)
        except (ValueError, FileExistsError) as e:
            self.query_one("#p-error", Static).update(str(e).capitalize() + ".")
            return
        self.dismiss(p.slug)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(None)


class BotSettingsScreen(Sheet):
    """The one bot every demo shares: which AI agent it presents as, and which
    features it runs with. Dismisses with {"persona", "slack_code", "tag"} or None."""

    def __init__(self, root: Path, features: dict[str, bool], bot_running: bool):
        super().__init__()
        self.root, self.features, self.bot_running = root, features, bot_running
        self.cur = personas.current(root)

    def compose(self) -> ComposeResult:
        with Vertical(classes="sheet"):
            yield Static("Bot settings", classes="sheet-title")
            yield Static("One bot runs every demo, so these apply to all of them.", classes="sheet-body")
            yield Static("Appears in Slack as", classes="section-label")
            yield Vertical(id="persona-box")
            yield link("＋ New persona…", id="new-persona")
            yield Static("", id="mismatch", classes="sheet-warn")
            yield Static("Features", classes="section-label")
            yield HorizontalGroup(Static("Slack Code", classes="setting-label"),
                                  Toggle(self.features["slack_code"], id="f-code"), classes="setting")
            yield Static("Build and fix requests open a code channel.", classes="caption")
            yield HorizontalGroup(Static("In-thread tasks (Claude Tag)", classes="setting-label"),
                                  Toggle(self.features["tag"], id="f-tag"), classes="setting")
            yield Static("Claude Tag phrases run a checklist in the thread.", classes="caption")
            yield Static("Applying restarts the bot if it's running. A new name shows in Slack within "
                         "about 10 seconds.", classes="sheet-note")
            if self.cur:
                yield Static("Picture", classes="section-label")
                yield Static("Slack doesn't let apps change their own picture, so upload it in your "
                             "app's settings.", classes="sheet-note")
                yield link("Open app settings ↗", id="app-settings")
            yield self.buttons(
                Button("Cancel", id="no", compact=True),
                Button("Apply & restart" if self.bot_running else "Apply", id="yes", compact=True, variant="primary"),
            )

    async def on_mount(self) -> None:
        await self._fill(self.cur.slug if self.cur else None)

    async def _fill(self, selected: str | None) -> None:
        box = self.query_one("#persona-box", Vertical)
        await box.remove_children()
        buttons = []
        for p in personas.list_personas(self.root):
            label = Text(p.slack_name)
            if p.name != p.slack_name:
                label.append(f"   talks as {p.name}", style=MUTED)
            buttons.append(Dot(label, value=p.slug == selected, name=p.slug))
        await box.mount(RadioSet(*buttons, id="persona-set"))
        active = personas.get(self.cur.slug, self.root) if self.cur else None
        warn = self.query_one("#mismatch", Static)
        if self.cur and active and not self.cur.matches(active):
            warn.update(f"Slack shows “{self.cur.slack_name}”, but it talks as {active.name}. "
                        "Pick one and apply to line them up.")
            warn.display = True
        else:
            warn.display = False

    def _selected(self) -> str | None:
        rs = self.query_one("#persona-set", RadioSet)
        return rs.pressed_button.name if rs.pressed_button else None

    @on(Button.Pressed, "#new-persona")
    def _new(self) -> None:
        async def created(slug):
            if slug:
                await self._fill(slug)
        self.app.push_screen(NewPersonaScreen(self.root), created)

    @on(Button.Pressed, "#app-settings")
    def _settings(self) -> None:
        open_external(personas.app_settings_url(self.cur.app_id))

    @on(Button.Pressed, "#yes")
    def _apply(self) -> None:
        self.dismiss({
            "persona": self._selected(),
            "slack_code": self.query_one("#f-code", Toggle).value,
            "tag": self.query_one("#f-tag", Toggle).value,
        })

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(None)
