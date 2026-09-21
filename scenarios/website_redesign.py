"""Scenario: WelloGuard homepage redesign (welloguard/marketing-site).

The DEFAULT Slack Code demo, and the one that shows the hero HTML artifact the
way Slack's launch videos do: the Preview tab is a REAL rendered landing page,
not a diagram about the change. Asking Claude to fix it (or /patch) re-renders
the SAME Preview tab so you watch the page change in place — no copy/paste/refresh.

Backstory (seeded by channels/seed_welloguard_redesign.py): design + eng +
product have been iterating on a homepage redesign in #welloguard-redesign. The
new hero section looks great but ships a real accessibility flaw — the primary
CTA is white text on the amber hero (~1.9:1 contrast, below WCAG AA 4.5:1), which
is unreadable for low-vision users. The team agrees to ship the redesign, so

    @Claude implement the homepage redesign

spins up the code channel. This module is the SINGLE source of truth for every
artifact so the page, the diff, the check, and the numbers can never disagree.

The one hero change: the page ships with the contrast flaw (preview_html(False)),
the contrast check FAILs, and the patch darkens the CTA label to near-black
(preview_html(True)) → a visible change in the same Preview tab → check PASSes.
"""
from __future__ import annotations

# --- Identity / context-bar facts --------------------------------------------
SLUG = "website_redesign"
REPO = "welloguard/marketing-site"
REPO_URL = "https://github.com/welloguard/marketing-site"
BRANCH_PREFIX = "feat/"
PR_URL = "https://github.com/welloguard/marketing-site/pull/128"

# The check this scenario runs → /check-contrast + the context-bar pass item.
CHECK_LABEL = "contrast"

# The Preview is the hero here: a real landing page. Plus the code diff and the
# conversion dashboard. (No recap tab — the recap posts as a message.)
ARTIFACTS = {"diff", "preview", "dashboard"}

# The artifact filename shown in the Code tab's diff banner. This scenario SEEDS
# a live-editable page: the session opens with preview_html(False) as turn-0
# content under this name, and follow-up edits ("make the hero blue") re-diff the
# agent's regenerated file against it. index.html reads as a homepage.
SEED_FILENAME = "index.html"


def seed_filename() -> str:
    """Filename for the seeded live artifact (the WelloGuard homepage)."""
    return SEED_FILENAME

# Brand palette — shared by the page and the diff/check so the numbers agree.
_AMBER = "#F0C808"          # hero band background
_CTA_TEXT_BAD = "#FFFFFF"   # white on amber ≈ 1.9:1 — the flaw
_CTA_TEXT_GOOD = "#1A1A1A"  # near-black on amber ≈ 10.8:1 — the fix
_INK = "#14203A"            # brand navy for headings/nav


# --- Code tab: the diff (pre-patch and post-patch) ---------------------------
def base_diff() -> str:
    """The Code tab: the homepage redesign landing on the new hero. A web diff
    (HTML + CSS), not backend code. The hero CTA is authored with the flaw in
    place — white label on the amber button — which the contrast check catches."""
    return (
        "diff --git a/src/index.html b/src/index.html\n"
        "--- a/src/index.html\n"
        "+++ b/src/index.html\n"
        "@@ -18,9 +18,14 @@\n"
        "   <section class=\"hero\">\n"
        "-    <h1>Security that keeps up.</h1>\n"
        "-    <p>Welcome to WelloGuard.</p>\n"
        "-    <a class=\"btn\" href=\"/signup\">Learn more</a>\n"
        "+    <h1>Stop threats before they reach your team.</h1>\n"
        "+    <p>WelloGuard watches every endpoint, so you don't have to.</p>\n"
        "+    <a class=\"btn btn--cta\" href=\"/signup\">Get started free</a>\n"
        "   </section>\n"
        "diff --git a/src/styles.css b/src/styles.css\n"
        "--- a/src/styles.css\n"
        "+++ b/src/styles.css\n"
        "@@ -40,6 +40,12 @@\n"
        f"   .hero {{ background: {_AMBER}; padding: 72px 24px; text-align: center; }}\n"
        "+  .btn--cta {\n"
        f"+    background: {_AMBER};\n"
        f"+    color: {_CTA_TEXT_BAD};   /* white on amber — high-contrast brand look */\n"
        "+    font-weight: 700;\n"
        "+    padding: 14px 28px;\n"
        "+  }\n"
    )


def patch_diff() -> str:
    """The patched diff after the contrast check fails: the CTA label goes from
    white to near-black on the same amber button — clears WCAG AA + AAA while
    keeping the brand color. One focused change, mirrored by preview_html(True)."""
    return (
        "diff --git a/src/styles.css b/src/styles.css\n"
        "--- a/src/styles.css\n"
        "+++ b/src/styles.css\n"
        "@@ -40,10 +40,10 @@\n"
        "   .btn--cta {\n"
        f"     background: {_AMBER};\n"
        f"-    color: {_CTA_TEXT_BAD};   /* white on amber — fails contrast (1.9:1) */\n"
        f"+    color: {_CTA_TEXT_GOOD};   /* near-black on amber — 10.8:1, clears AA + AAA */\n"
        "     font-weight: 700;\n"
        "     padding: 14px 28px;\n"
        "   }\n"
    )


# --- Preview tab: the REAL landing page (self-contained HTML) ----------------
# ONE self-contained document: a single inline <style> block, zero external
# assets/fonts/JS (the html view renders one string with no network). This is the
# actual WelloGuard homepage — sticky nav, hero with the CTA, a 3-card feature
# row, a testimonial, a footer. preview_html(False) ships the low-contrast CTA
# (white on amber); preview_html(True) darkens the CTA label — a VISIBLE change
# to the same page after the patch.
def _page_css(cta_text: str) -> str:
    return f"""
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font: 16px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
          color: {_INK}; background: #fff; }}
  nav {{ position: sticky; top: 0; display: flex; align-items: center; justify-content: space-between;
         padding: 16px 40px; background: #fff; border-bottom: 1px solid #ececef; }}
  nav .brand {{ font-weight: 800; font-size: 20px; letter-spacing: -.02em; }}
  nav .brand span {{ color: {_AMBER}; }}
  nav .links a {{ color: {_INK}; text-decoration: none; margin-left: 28px; font-weight: 600; font-size: 15px; }}
  .hero {{ background: {_AMBER}; padding: 88px 24px 96px; text-align: center; }}
  .hero h1 {{ font-size: 44px; line-height: 1.1; letter-spacing: -.02em; max-width: 720px; margin: 0 auto 16px; }}
  .hero p {{ font-size: 19px; max-width: 560px; margin: 0 auto 32px; color: #3a3320; }}
  .btn--cta {{ display: inline-block; background: {_AMBER}; color: {cta_text};
               border: 2px solid {cta_text}; font-weight: 700; font-size: 17px;
               padding: 14px 30px; border-radius: 10px; text-decoration: none; }}
  .features {{ display: flex; gap: 24px; max-width: 1000px; margin: 72px auto; padding: 0 24px; }}
  .card {{ flex: 1; border: 1px solid #ececef; border-radius: 14px; padding: 28px; }}
  .card .ic {{ width: 40px; height: 40px; border-radius: 10px; background: {_AMBER}; margin-bottom: 14px; }}
  .card h3 {{ font-size: 18px; margin-bottom: 8px; }}
  .card p {{ font-size: 15px; color: #5b6172; }}
  .quote {{ max-width: 720px; margin: 0 auto 80px; padding: 0 24px; text-align: center; }}
  .quote blockquote {{ font-size: 22px; line-height: 1.4; letter-spacing: -.01em; }}
  .quote cite {{ display: block; margin-top: 16px; font-size: 14px; color: #5b6172; font-style: normal; }}
  footer {{ border-top: 1px solid #ececef; padding: 32px 40px; font-size: 13px; color: #5b6172;
            display: flex; justify-content: space-between; }}
"""


# --- FAQ accordion (the marketer's "Jennifer edit") --------------------------
# A marketer wants an expandable FAQ on the page — normally an eng ticket. It's a
# real, interactive feature but pure CSS (<details>/<summary>, no JS), so it renders
# AND behaves in the Preview tab's no-JS html view. Served instantly as a pre-baked
# diff + preview (see slack_code.run_faq_edit); `faq=True` composes with `patched`.
_FAQ_Q = [
    ("How fast can we deploy across the org?",
     "Most teams are fully rolled out in an afternoon — there are no agents to install "
     "or babysit."),
    ("Does WelloGuard work with our existing tools?",
     "Yes. WelloGuard integrates with the identity, SIEM, and ticketing tools you "
     "already run, so alerts land where your team already works."),
    ("What happens when a threat is detected?",
     "Threats are contained automatically in seconds, and your team gets a single "
     "clear notification with the full context — no alert storm."),
]


def _faq_css() -> str:
    return f"""
  .faq {{ max-width: 720px; margin: 0 auto 80px; padding: 0 24px; }}
  .faq h2 {{ font-size: 28px; letter-spacing: -.02em; text-align: center; margin-bottom: 28px; }}
  .faq details {{ border: 1px solid #ececef; border-radius: 12px; padding: 4px 20px; margin-bottom: 12px; }}
  .faq summary {{ cursor: pointer; list-style: none; padding: 16px 0; font-weight: 700;
                  font-size: 17px; color: {_INK}; display: flex; justify-content: space-between; }}
  .faq summary::-webkit-details-marker {{ display: none; }}
  .faq summary::after {{ content: "+"; color: {_AMBER}; font-size: 22px; line-height: 1; }}
  .faq details[open] summary::after {{ content: "\\2212"; }}
  .faq details p {{ padding: 0 0 18px; color: #5b6172; font-size: 15px; }}
"""


def _faq_section_html() -> str:
    items = "".join(
        f"<details><summary>{q}</summary><p>{a}</p></details>" for (q, a) in _FAQ_Q
    )
    return (
        "<section class=\"faq\">"
        "<h2>Frequently asked questions</h2>"
        f"{items}"
        "</section>"
    )


def faq_diff() -> str:
    """The Code tab for the FAQ edit: a CSS-only expandable FAQ section added to the
    homepage — new markup in index.html + `.faq` rules in styles.css. No JS. Mirrors
    the base/patch diff shape; published instantly by slack_code.run_faq_edit."""
    return (
        "diff --git a/src/index.html b/src/index.html\n"
        "--- a/src/index.html\n"
        "+++ b/src/index.html\n"
        "@@ -34,6 +34,17 @@\n"
        "     <cite>\u2014 Priya Anand, Head of Security, Northwind</cite>\n"
        "   </section>\n"
        "+  <section class=\"faq\">\n"
        "+    <h2>Frequently asked questions</h2>\n"
        "+    <details>\n"
        "+      <summary>How fast can we deploy across the org?</summary>\n"
        "+      <p>Most teams are fully rolled out in an afternoon \u2014 no agents to babysit.</p>\n"
        "+    </details>\n"
        "+    <details>\n"
        "+      <summary>Does WelloGuard work with our existing tools?</summary>\n"
        "+      <p>Yes \u2014 identity, SIEM, and ticketing tools you already run.</p>\n"
        "+    </details>\n"
        "   <footer>\n"
        "diff --git a/src/styles.css b/src/styles.css\n"
        "--- a/src/styles.css\n"
        "+++ b/src/styles.css\n"
        "@@ -60,6 +60,15 @@\n"
        "   footer { border-top: 1px solid #ececef; padding: 32px 40px; }\n"
        "+  /* Expandable FAQ — pure CSS, no JS (uses <details>/<summary>). */\n"
        "+  .faq { max-width: 720px; margin: 0 auto 80px; padding: 0 24px; }\n"
        "+  .faq details { border: 1px solid #ececef; border-radius: 12px; padding: 4px 20px;\n"
        "+                 margin-bottom: 12px; }\n"
        "+  .faq summary { cursor: pointer; font-weight: 700; padding: 16px 0; }\n"
        f"+  .faq summary::after {{ content: \"+\"; color: {_AMBER}; }}\n"
        "+  .faq details[open] summary::after { content: \"\\2212\"; }\n"
    )


def preview_html(patched: bool = False, *, faq: bool = False) -> str:
    """The Preview tab: the WelloGuard homepage. ``patched`` darkens the CTA label
    (the contrast fix) — the one visible change between the flawed and fixed page.
    ``faq`` (keyword-only, opt-in) adds the pre-baked expandable FAQ accordion just
    above the footer; it composes with ``patched`` (the marketer's edit doesn't
    change the CTA-contrast state)."""
    cta_text = _CTA_TEXT_GOOD if patched else _CTA_TEXT_BAD
    style = _page_css(cta_text) + (_faq_css() if faq else "")
    faq_section = _faq_section_html() if faq else ""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>WelloGuard</title><style>{style}</style></head><body>"
        # nav
        "<nav><div class=\"brand\">Wello<span>Guard</span></div>"
        "<div class=\"links\"><a href=\"#\">Product</a><a href=\"#\">Pricing</a>"
        "<a href=\"#\">Docs</a><a href=\"#\">Sign in</a></div></nav>"
        # hero (the redesigned section — with the CTA under test)
        "<section class=\"hero\">"
        "<h1>Stop threats before they reach your team.</h1>"
        "<p>WelloGuard watches every endpoint, so you don't have to.</p>"
        "<a class=\"btn--cta\" href=\"/signup\">Get started free</a>"
        "</section>"
        # feature cards
        "<section class=\"features\">"
        "<div class=\"card\"><div class=\"ic\"></div><h3>Always-on monitoring</h3>"
        "<p>Every device, every login, watched around the clock.</p></div>"
        "<div class=\"card\"><div class=\"ic\"></div><h3>Instant response</h3>"
        "<p>Threats contained in seconds, not the next business day.</p></div>"
        "<div class=\"card\"><div class=\"ic\"></div><h3>Zero setup</h3>"
        "<p>Deploy across your org in an afternoon, no agents to babysit.</p></div>"
        "</section>"
        # testimonial
        "<section class=\"quote\"><blockquote>“We caught an intrusion the same morning "
        "we rolled WelloGuard out. It paid for itself on day one.”</blockquote>"
        "<cite>— Priya Anand, Head of Security, Northwind</cite></section>"
        # FAQ accordion (only when faq=True — the marketer's instant edit)
        f"{faq_section}"
        # footer
        "<footer><div>© 2026 WelloGuard, Inc.</div>"
        "<div>Privacy · Terms · Status</div></footer>"
        "</body></html>"
    )


# --- Check verdicts: the contrast fail → patch → clean pass loop -------------
# Scripted + deterministic (the agent does NOT compute contrast) so the beat is
# identical every run. Posted top-level via chat.postMessage, which always
# renders regardless of view state. mrkdwn: single-asterisk bold.
def check_fail_report() -> str:
    return (
        ":red_circle: *Accessibility check — 1 failure*\n"
        f"• Hero CTA `Get started free`: `{_CTA_TEXT_BAD}` text on `{_AMBER}` ≈ *1.9:1* contrast, "
        "below the *4.5:1* WCAG AA minimum.\n"
        "> The primary call-to-action is unreadable for low-vision users. That's not a nitpick — "
        "it ships the homepage broken for real visitors.\n"
        "Want me to patch it? I'll darken the label to near-black — brand amber intact, AA compliant."
    )


def check_pass_report() -> str:
    return (
        ":large_green_circle: *Accessibility check — clean pass*\n"
        f"• Hero CTA now `{_CTA_TEXT_GOOD}` text on `{_AMBER}` ≈ *10.8:1* (clears AA *and* AAA).\n"
        "• Brand amber unchanged; only the label color moved.\n"
        "0 failures. Safe to ship."
    )


# --- Dashboard tab: conversion metrics (Block Kit) ---------------------------
# The video's "is it working?" numbers — old homepage vs the redesign. Single
# source of truth for the recap so the deck matches. (label, before, after, delta)
DASHBOARD_ROWS: list[tuple[str, str, str, str]] = [
    ("Signup conversion", "3.1%", "*4.7%*", ":arrow_up: +52%"),
    ("Weekly signups", "1,240", "*1,910*", ":arrow_up: +54%"),
    ("Bounce rate", "54%", "*41%*", ":arrow_down: -13pts"),
    ("Time to signup", "2m10s", "*1m22s*", ":arrow_down: -37%"),
]


def dashboard_blocks() -> list[dict]:
    """The Dashboard tab as a Block Kit blocks list. Published via
    set_view(view_type='block_kit', blocks=<ARRAY>) — a real array, not a
    json.dumps'd string. mrkdwn bold is single-asterisk."""
    rows = "\n".join(
        f"• *{label}*  `{before}` → {after}   _{delta}_"
        for (label, before, after, delta) in DASHBOARD_ROWS
    )
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "WelloGuard homepage — conversion (old vs new)"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": rows}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "Last 14 days · `welloguard/marketing-site` · scoped to channel members"},
        ]},
    ]


# --- Recap: the 5-part leadership recap (posted as a message) ----------------
def recap_canvas() -> str:
    """The recap — 5 slide-like sections built from the SAME conversion numbers as
    the dashboard. run_make_recap bolds the `##` headings and posts it as a
    message (not a canvas view tab)."""
    return "\n\n".join([
        "## 1 · The ask",
        "Ship the homepage redesign before the noon go-live — a cleaner hero, a stronger CTA — "
        "without a war room or a launch meeting.",

        "## 2 · What shipped",
        f"Redesigned the WelloGuard homepage hero + CTA in {REPO}. Draft PR: {PR_URL}.",

        "## 3 · Caught before customers",
        "An accessibility check flagged the new CTA at *1.9:1* contrast (white on amber) — below "
        "WCAG AA. Patched the label to near-black (*10.8:1*, brand color intact) and re-checked: "
        "*clean pass*. The page shipped accessible.",

        "## 4 · It's working",
        "• Signup conversion: `3.1%` → *4.7%* (+52%)\n"
        "• Weekly signups: `1,240` → *1,910* (+54%)\n"
        "• Bounce rate: `54%` → *41%* (-13pts)",

        "## 5 · Next steps",
        "Merge the draft PR, keep the conversion dashboard pinned through launch week, then apply "
        "the same hero pattern to the pricing and product pages.",
    ])


# --- Decision provenance: injected into the session for follow-up recall -----
def provenance() -> dict[str, str]:
    """Decision → rationale. Rendered into the FIRST agent turn's input so a later
    '@Claude why …?' answers from recorded reasoning, not improvisation."""
    return {
        "single_hero_layout": (
            "I led with a single benefit-driven hero over the old three-column splash because "
            "Design's thread note said the splash felt cluttered and buried the CTA; one clear "
            "message above the fold converts better."
        ),
        "cta_above_fold": (
            "Kept the primary CTA in the hero, above the fold, so the first thing a visitor can do "
            "is the thing we want them to do — start a free trial."
        ),
        "kept_brand_amber": (
            "Kept the brand amber on the CTA button and fixed the contrast by darkening only the "
            "label (white → near-black). Brand recognition intact, and it clears WCAG AA + AAA — "
            "better than swapping to an off-brand button color."
        ),
        "headline_copy": (
            "Rewrote the headline to lead with the outcome ('Stop threats before they reach your "
            "team') instead of the product name — the name is in the nav; the hero should sell the "
            "benefit."
        ),
    }


def render_provenance() -> str:
    """A compact block of the provenance facts, for priming the session's first
    agent turn (so a later 'why did you …' answers from these, in the bot's own
    words)."""
    lines = ["Decisions you made on this redesign and why (use these if asked to explain your choices):"]
    for key, why in provenance().items():
        lines.append(f"- {key}: {why}")
    return "\n".join(lines)


# --- Thinking-pulse lines (see checkout_incident.thinking_line for the pattern). ---
_THINKING = {
    "open": "Reading the redesign thread and shipping the new hero + CTA…",
    "check": "Checking the hero CTA's color contrast against the WCAG AA threshold…",
    "patch": "Darkening the CTA label to hit AA while keeping the brand amber…",
    "metrics": "Pulling the conversion numbers — signups, bounce, before vs after…",
    "recap": "Writing the leadership recap — the ask, what shipped, the results…",
}


def thinking_line(action: str) -> str:
    """The Thinking-stream line for a scripted action, or a generic fallback."""
    return _THINKING.get(action, "Working on it…")


# --- Human participants: who carries in from the origin thread, and their beats -
# Cindy (the design lead who shipped the single benefit-led hero) and Ralph (growth
# / conversion, who watches whether the CTA converts) join Claude. Ralph's beat
# ties directly to the contrast check — Adam flagged the white-on-amber CTA in the
# origin thread. `name` = spoof username; `email_stem` = invite-tier persona stem.
PARTICIPANTS = [
    {"name": "Cindy", "email_stem": "cindy_central", "role": "design lead"},
    {"name": "Ralph", "email_stem": "ralph_clark", "role": "growth / conversion"},
]


def participant_beats() -> list[dict]:
    """Scripted human chime-ins (arrival = stall; on_artifacts = react to the diff)."""
    return [
        {"name": "Cindy", "phase": "arrival",
         "text": (":art: This is the single benefit-led hero — headline plus one CTA. "
                  "Let's see what Claude ships.")},
        {"name": "Ralph", "phase": "on_artifacts",
         "text": ("Looks sharp. Only thing I'll watch is the CTA contrast on the amber band — "
                  "it has to pass before this goes live.")},
    ]
