"""The demo manager's colours: synthwave — neon cyan, blue and violet on deep
navy, hot pink for the thing to press. Everything else (app.tcss, the theme,
the banner) takes its colours from the roles below, so changing the scheme
means changing this file only.
"""
from __future__ import annotations

from rich.text import Text

# ---- the scheme ----------------------------------------------------------------

BG = "#070B1F"        # the window
PANEL = "#0D1430"     # sidebar, bars, sheets
SURFACE = "#16204A"   # buttons, inputs
LINE = "#23305E"      # borders and rules
TEXT = "#E6F1FF"
MUTED = "#7F8BB3"

CYAN = "#00F0FF"
BLUE = "#3D5AFE"
VIOLET = "#B14DFF"
PINK = "#FF2E97"
YELLOW = "#FFD23F"
RED = "#FF4D6D"

# The banner, top row to bottom: cyan → electric blue → violet.
GRADIENT = ("#00F0FF", "#00C2FF", "#0091FF", "#3D5AFE", "#7C4DFF", "#B14DFF")

# ---- what each colour means ------------------------------------------------------

GOOD = CYAN        # done, running, ON
LINK = CYAN        # text buttons
ACCENT = PINK      # the next step, the phrase to type, primary buttons
TITLE = CYAN       # a demo's name, sheet titles
SUBTITLE = VIOLET  # customer · surface
SECTION = PINK     # ▓▒░ SECTION headers
PHRASE = VIOLET    # trigger phrase chips
FRAME = VIOLET     # the sidebar's border
SHEET = PINK       # pop-up borders
WARN = YELLOW
DANGER = RED       # destructive actions only

# CSS variables for app.tcss ($line, $muted, …), registered with the theme.
CSS_VARIABLES = {
    "line": LINE, "muted": MUTED, "link": LINK, "accent-text": BG, "title": TITLE,
    "subtitle": SUBTITLE, "frame": FRAME, "sheet": SHEET, "good": GOOD, "warn": WARN, "danger": DANGER,
    "accent-hover": "#FF5CAD",
}


# ---- helpers ---------------------------------------------------------------------

def _rgb(hex_: str) -> tuple[int, int, int]:
    return int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)


def mix(a: str, b: str, t: float) -> str:
    """The colour ``t`` of the way from ``a`` to ``b``."""
    (r1, g1, b1), (r2, g2, b2) = _rgb(a), _rgb(b)
    return "#{:02X}{:02X}{:02X}".format(round(r1 + (r2 - r1) * t), round(g1 + (g2 - g1) * t), round(b1 + (b2 - b1) * t))


def along(t: float, stops: tuple[str, ...] = GRADIENT) -> str:
    """The gradient's colour at ``t`` (0 → first stop, 1 → last)."""
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    return mix(stops[i], stops[i + 1], t - i)


def gradient(text: str, stops: tuple[str, ...] = GRADIENT, style: str = "") -> Text:
    """``text`` coloured left to right along the gradient."""
    out = Text()
    n = max(len(text) - 1, 1)
    for i, ch in enumerate(text):
        out.append(ch, style=f"{style} {along(i / n, stops)}".strip())
    return out


def fade(stops: tuple[str, ...] = (CYAN, BLUE, VIOLET)) -> Text:
    """``▓▒░``, the section marker."""
    return Text.assemble(*((ch, c) for ch, c in zip("▓▒░", stops)))
