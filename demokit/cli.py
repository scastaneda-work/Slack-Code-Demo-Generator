"""``./demo <command>`` — the demo manager from the command line.

The TUI (``./demo`` with no arguments) and Claude Code's skills use the same
library underneath, so anything the TUI does can be done here too.
"""
from __future__ import annotations

import argparse
import sys

from demokit import actions, health, personas, routing, state, trash
from demokit.manifest import Demo, load_demos


def _find(demos: list[Demo], demo_id: str) -> Demo:
    demo = next((d for d in demos if d.id == demo_id), None)
    if demo is None:
        sys.exit(f"[demo] No demo called '{demo_id}'. Run ./demo list to see them.")
    return demo


def _mark(c: health.Check) -> str:
    return "✓" if c.ok else ("!" if c.warn else "✗")


def cmd_list(args) -> int:
    demos = load_demos()
    hidden = state.hidden_demo_ids()
    width = max((len(d.id) for d in demos), default=10)
    section = None
    for d in demos:
        if d.example != section:
            section = d.example
            print("\nSample demos" if d.example else "Your demos")
        bad = sum(1 for c in health.demo_checks(d, demos) if not c.ok and not c.warn)
        status = "hidden" if d.id in hidden else (f"{bad} problem(s)" if bad else "ok")
        print(f"  {d.id:<{width}}  {d.name}  [{', '.join(d.surfaces) or '-'}]  {status}")
    return 0


def cmd_show(args) -> int:
    demos = load_demos()
    d = _find(demos, args.id)
    print(f"{d.name}  ({d.id}{', hidden' if d.id in state.hidden_demo_ids() else ''})")
    if d.summary:
        print(f"  {d.summary}")
    if d.trigger:
        print(f"  Trigger:  @<bot> {d.trigger}")
    if phrases := routing.phrases(d):
        print(f"  Phrases:  {', '.join(phrases)}")
    if d.channels:
        print(f"  Channels: {', '.join('#' + c for c in d.channels)}")
    for doc in d.docs:
        print(f"  Doc:      {doc.label} — {doc.path}")
    if d.actions:
        print("  Actions:")
        for a in d.actions:
            last = state.last_run(d.id, a.id)
            when = f"  (last run {actions.ago(last['started'])})" if last else ""
            print(f"    {a.id}: {a.label}{' (preview, changes nothing)' if a.preview else ''}{when}")
            for o in a.options:
                print(f"        {o.flag}  {o.label}")
    print("  Health:")
    for c in health.demo_checks(d, demos):
        print(f"    {_mark(c)} {c.label}{' — ' + c.detail if c.detail and not c.ok else ''}")
    return 0


def cmd_run(args) -> int:
    demos = load_demos()
    d = _find(demos, args.id)
    a = d.action(args.action)
    if a is None:
        sys.exit(f"[demo] '{d.id}' has no action '{args.action}'. Its actions: "
                 f"{', '.join(x.id for x in d.actions) or 'none'}.")
    if (a.destructive or a.confirm) and not args.yes:
        print(f"[demo] {a.confirm or a.label + ' changes things in Slack that are hard to undo.'}")
        sys.exit("[demo] Re-run with --yes to go ahead.")
    last = state.last_run(d.id, a.id)
    if last and a.repeat_warning:
        print(f"[demo] Note: last run {actions.ago(last['started'])}. {a.repeat_warning}")
    try:
        code = actions.run_action(d, a, args.flags)
    except ValueError as e:
        sys.exit(f"[demo] {e}")
    print(f"[demo] {'Done' if code == 0 else f'Failed (exit {code}) — read the output above'}: {a.label}")
    return code


def cmd_route(args) -> int:
    r = routing.preview(args.phrase, load_demos())
    print(r.explanation)
    if r.scenario:
        print(f"  scenario: {r.scenario}")
    return 0


def cmd_hide(args, hidden: bool) -> int:
    d = _find(load_demos(), args.id)
    state.set_hidden(d.id, d.scenarios, d.tag_scenarios, hidden)
    print(f"[demo] {d.name} is {'hidden — its trigger phrases no longer open it' if hidden else 'visible again'}.")
    return 0


def cmd_delete(args) -> int:
    demos = load_demos()
    d = _find(demos, args.id)
    try:
        plan = trash.plan_delete(d, demos)
    except ValueError as e:
        sys.exit(f"[demo] {e} (./demo hide {d.id}).")
    print(f"Deleting {d.name} moves these to .trash/ (restorable with ./demo restore):")
    for rel in plan.move:
        print(f"  {rel}")
    for rel, other in plan.kept_shared:
        print(f"  kept: {rel} (also used by {other})")
    if not args.yes:
        sys.exit("[demo] Nothing changed. Re-run with --yes to delete.")
    entry = trash.delete(plan)
    print(f"[demo] Deleted. Restore with: ./demo restore {entry.name}")
    print("[demo] If the bot is running, restart it so it drops this demo.")
    return 0


def cmd_trash(args) -> int:
    entries = trash.list_trash()
    if not entries:
        print("Trash is empty.")
    for e in entries:
        print(f"  {e.name}  {e.demo_name} — {len(e.files)} file(s), deleted {actions.ago(e.deleted_at)}")
    return 0


def cmd_restore(args) -> int:
    try:
        e = trash.restore(args.entry)
    except KeyError:
        sys.exit(f"[demo] No trash entry '{args.entry}'. Run ./demo trash to see them.")
    except trash.RestoreConflict as c:
        sys.exit("[demo] Nothing restored — these exist again:\n  " + "\n  ".join(c.paths))
    print(f"[demo] Restored {e.demo_name} ({len(e.files)} file(s)). Restart the bot if it's running.")
    return 0


def cmd_check(args) -> int:
    demos = load_demos()
    errors = 0
    print("This machine:")
    for c in health.setup_checks():
        errors += not c.ok and not c.warn
        print(f"  {_mark(c)} {c.label}{' — ' + c.detail if not c.ok else ''}")
    print("Demos:")
    for c in health.repo_checks(demos):
        errors += not c.ok and not c.warn
        print(f"  {_mark(c)} {c.label} — {c.detail}")
    for d in demos:
        bad = [c for c in health.demo_checks(d, demos) if not c.ok]
        errors += sum(not c.warn for c in bad)
        print(f"  {'✓' if not bad else '✗'} {d.id}")
        for c in bad:
            print(f"      {_mark(c)} {c.label}{' — ' + c.detail if c.detail else ''}")
    last = state.last_self_test()
    if last:
        print(f"Last self-test: {last['passed']} passed, {last['failed']} failed ({actions.ago(last['at'])}).")
    return 1 if errors else 0


def cmd_self_test(args) -> int:
    print("[demo] Running qa.py --self-test (about 30 seconds)…")
    r = actions.self_test()
    for line in r.problems:
        print(line)
    print(f"[demo] Self-test: {r.passed} passed, {r.failed} failed.")
    return r.exit_code


def cmd_persona(args) -> int:
    cur = personas.current()
    if args.persona_cmd == "use":
        try:
            p = personas.apply(args.slug)
        except (KeyError, RuntimeError) as e:
            sys.exit(f"[demo] {e}")
        print(f"[demo] The bot is now {p.slack_name}. Restart it to take effect, then update its "
              f"picture by hand: {personas.app_settings_url(cur.app_id) if cur else 'your app settings'}")
        return 0
    if args.persona_cmd == "new":
        try:
            p = personas.create(args.name, args.maker)
        except (ValueError, FileExistsError) as e:
            sys.exit(f"[demo] {e}")
        print(f"[demo] Created personas/{p.slug}.md. Switch to it with: ./demo persona use {p.slug}")
        return 0
    for p in personas.list_personas():
        mark = "●" if cur and cur.matches(p) else ("◐" if cur and cur.slug == p.slug else " ")
        print(f"  {mark} {p.slug:<20} appears in Slack as {p.slack_name}")
    if cur:
        active = personas.get(cur.slug)
        if active and not cur.matches(active):
            print(f"\n  Slack shows '{cur.slack_name}' but it talks as {active.name}. "
                  f"Fix with: ./demo persona use <slug>")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="./demo", description="Manage your Slack Code demos. "
                                "Run ./demo with no command for the terminal UI.")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")
    sub.add_parser("list", help="list demos").set_defaults(fn=cmd_list)
    s = sub.add_parser("show", help="details, actions and health for one demo")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("run", help="run one of a demo's actions (e.g. post its backstory)")
    s.add_argument("id")
    s.add_argument("action")
    s.add_argument("--yes", action="store_true", help="confirm a destructive action")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("route", help="which demo would this @-mention phrase open?")
    s.add_argument("phrase")
    s.set_defaults(fn=cmd_route)
    s = sub.add_parser("hide", help="stop a demo's trigger phrases from opening it")
    s.add_argument("id")
    s.set_defaults(fn=lambda a: cmd_hide(a, True))
    s = sub.add_parser("unhide", help="undo hide")
    s.add_argument("id")
    s.set_defaults(fn=lambda a: cmd_hide(a, False))
    s = sub.add_parser("delete", help="move a demo's files to .trash/ (restorable)")
    s.add_argument("id")
    s.add_argument("--yes", action="store_true", help="actually delete (otherwise just show what would move)")
    s.set_defaults(fn=cmd_delete)
    sub.add_parser("trash", help="list deleted demos").set_defaults(fn=cmd_trash)
    s = sub.add_parser("restore", help="bring a deleted demo back")
    s.add_argument("entry", help="the name shown by ./demo trash")
    s.set_defaults(fn=cmd_restore)
    sub.add_parser("check", help="check this machine's setup and every demo").set_defaults(fn=cmd_check)
    sub.add_parser("self-test", help="run qa.py --self-test and record the result").set_defaults(fn=cmd_self_test)
    s = sub.add_parser("persona", help="which AI agent the bot presents as (list / use / new)")
    s.set_defaults(fn=cmd_persona, persona_cmd=None)
    psub = s.add_subparsers(dest="persona_cmd", metavar="<list|use|new>")
    psub.add_parser("list", help="list personas (default)")
    u = psub.add_parser("use", help="switch the bot to a persona")
    u.add_argument("slug")
    n = psub.add_parser("new", help="create a persona from the Claude one")
    n.add_argument("name", help='e.g. "GitHub Copilot"')
    n.add_argument("--maker", required=True, help='who makes it, e.g. "GitHub"')
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # Unknown --flags after `run <id> <action>` are that action's options.
    args, extra = parser.parse_known_args(argv)
    if args.cmd == "run":
        args.flags = extra
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if not args.cmd:
        from demokit.tui import launch
        return launch()
    return args.fn(args)
