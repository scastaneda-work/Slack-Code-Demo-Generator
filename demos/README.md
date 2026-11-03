# Demo manifests

One `<id>.toml` per demo. A manifest is what makes a set of files *one demo*:
`./demo` lists it, runs its actions, hides it, and deletes it (restorably) from
this file alone. The file name is the demo's id (kebab-case, e.g.
`acme-checkout-redesign`).

`qa.py --self-test` checks every manifest: it parses, everything it points at
exists, and its trigger phrase opens its own story.

## Private demos: `custom/<id>/`

A demo for a real customer goes in its own folder here, `custom/<id>/`, laid
out like the repo. `demos/custom/` is git-ignored, so the whole demo stays on
your machine and no shared file names the customer.

```
demos/custom/acme-checkout-redesign/
├── demo.toml                                  the manifest (same fields as below)
├── scenarios/acme_checkout_redesign.py        Slack Code story; art in scenarios/assets/
├── scenarios_tag/<slug>.py                    Claude Tag story, if it has one
├── channels/seed_acme_checkout_redesign.py    seed script
└── docs/                                      click path, talk track, build plan
```

- The folder name is the demo's id.
- Paths in `demo.toml` are still repo-relative:
  `run = ["demos/custom/acme-checkout-redesign/channels/seed_acme_checkout_redesign.py", "--create"]`.
- The demo owns everything in its folder, so `files` is only for files outside it.
- The bot finds the stories in `scenarios/` and `scenarios_tag/` by slug, like the
  shared ones, so slugs must be unique across both places.
- A seed script finds the repo root by looking up for `config.py` (copy the one
  in `channels/seed_welloguard_redesign.py`), not by going up one folder.

## Fields

| Field | Required | What it is |
|---|---|---|
| `name` | yes | The label in `./demo`. |
| `customer` | | Groups the list. |
| `summary` | | One or two sentences: the story, and the flaw the check catches. |
| `surfaces` | | Any of `slack_code`, `claude_tag`, `slack` (plain seeded messages, no bot story). |
| `scenarios` | | Slack Code stories this demo owns: `scenarios/<slug>.py`. |
| `tag_scenarios` | | Claude Tag stories this demo owns: `scenarios_tag/<slug>.py`. |
| `trigger` | if it owns a scenario | What the SE types after `@<bot>` to start it. Must route to this demo's scenario. |
| `channels` | | Slack channels the demo's seeds create. `./demo` links to them so you can open or archive them in Slack. |
| `example` | | `true` for built-in examples: they can be hidden but not deleted. |
| `files` | | Other files this demo owns: seed scripts, `docs/demos/<id>/`, asset globs. They move to `.trash/` with the demo. A file another manifest also lists is kept. A private demo owns its whole folder without listing it. |
| `docs` | | `{ label, path }` entries: click path, talk track, build plan. They're under *Docs* on the demo's page in `./demo` (Markdown opens right there, anything else in its own app), and `./demo show <id>` lists them. |
| `[[actions]]` | | Buttons in `./demo` (see below). |

## Actions

Each `[[actions]]` entry runs one seed script with the venv's Python from the repo
root.

| Field | What it is |
|---|---|
| `id` | Used on the command line: `./demo run <demo-id> <action-id>`. |
| `label` | The button text. |
| `run` | `["channels/seed_<x>.py", "--fixed", "--args"]`. |
| `note` | One line under the button: what it does. |
| `options` | `{ flag, label }` checkboxes, e.g. `{ flag = "--invite", label = "Also invite the real cast" }`. Only these flags can be added. |
| `destructive` | `true` asks for a confirm first (CLI: `--yes`). |
| `confirm` | Custom confirm text (also forces a confirm). |
| `repeat_warning` | Shown when the action has run before, e.g. "posts a second copy of the thread". |
| `preview` | `true` for a dry run that changes nothing in Slack (e.g. `--dry-run`). `./demo` opens its output in a window as it runs. |
| `step` | First action only: the step title in `./demo`, e.g. "Set the scene". |

Seed scripts own their messages. An action only runs the script, so the story
stays word for word.

## Example

```toml
name      = "Acme · Checkout redesign"
customer  = "Acme"
summary   = "A product team prototypes the new checkout in Slack Code; the accessibility check catches the unlabeled card field."
surfaces  = ["slack_code"]
scenarios = ["acme_checkout_redesign"]
trigger   = "build the acme checkout redesign"
channels  = ["acme-checkout"]
files = [
  "channels/seed_acme_checkout_redesign.py",
  "docs/demos/acme-checkout-redesign/",
  "scenarios/assets/acme_*",
]
docs = [
  { label = "Click path", path = "docs/demos/acme-checkout-redesign/click-path.html" },
]

[[actions]]
id      = "post-backstory"
label   = "Post backstory to #acme-checkout"
run     = ["channels/seed_acme_checkout_redesign.py", "--create"]
options = [{ flag = "--invite", label = "Also invite the real cast to the channel" }]
repeat_warning = "It posts a second copy of the thread if the old one is still there."
```
