"""The block-letter banner across the top of ./demo.

Letters are drawn in the "ANSI Shadow" style: solid blocks with a box-drawn
shadow. Each row takes the next colour of the gradient (cyan at the top,
violet at the bottom) and the shadow lines a dimmer shade of it, then a
gradient rule runs underneath. Windows too narrow for those letters get the
same words in a two-row block font (SMALL), coloured left to right; the app
hides the banner when the window is too short for it.

``svg()`` draws the same banner for the README (docs/banner.svg). Regenerate it
after changing the letters or colours:

    .venv/bin/python -m demokit.tui.banner > docs/banner.svg
"""
from __future__ import annotations

import sys

from rich.text import Text
from textual.events import Resize
from textual.widget import Widget

from demokit.tui.palette import BG, GRADIENT, along, mix

FONT: dict[str, tuple[str, ...]] = {
    "A": (" █████╗ ",
          "██╔══██╗",
          "███████║",
          "██╔══██║",
          "██║  ██║",
          "╚═╝  ╚═╝"),
    "C": (" ██████╗",
          "██╔════╝",
          "██║     ",
          "██║     ",
          "╚██████╗",
          " ╚═════╝"),
    "D": ("██████╗ ",
          "██╔══██╗",
          "██║  ██║",
          "██║  ██║",
          "██████╔╝",
          "╚═════╝ "),
    "E": ("███████╗",
          "██╔════╝",
          "█████╗  ",
          "██╔══╝  ",
          "███████╗",
          "╚══════╝"),
    "K": ("██╗  ██╗",
          "██║ ██╔╝",
          "█████╔╝ ",
          "██╔═██╗ ",
          "██║  ██╗",
          "╚═╝  ╚═╝"),
    "L": ("██╗     ",
          "██║     ",
          "██║     ",
          "██║     ",
          "███████╗",
          "╚══════╝"),
    "M": ("███╗   ███╗",
          "████╗ ████║",
          "██╔████╔██║",
          "██║╚██╔╝██║",
          "██║ ╚═╝ ██║",
          "╚═╝     ╚═╝"),
    "O": (" ██████╗ ",
          "██╔═══██╗",
          "██║   ██║",
          "██║   ██║",
          "╚██████╔╝",
          " ╚═════╝ "),
    "S": ("███████╗",
          "██╔════╝",
          "███████╗",
          "╚════██║",
          "███████║",
          "╚══════╝"),
    " ": ("  ",) * 6,
}
ROWS = 6

# The same letters two rows tall, for windows too narrow for FONT.
SMALL: dict[str, tuple[str, str]] = {
    "A": ("▄▀█", "█▀█"),
    "C": ("█▀▀", "█▄▄"),
    "D": ("█▀▄", "█▄▀"),
    "E": ("█▀▀", "██▄"),
    "K": ("█▄▀", "█ █"),
    "L": ("█  ", "█▄▄"),
    "M": ("█▀▄▀█", "█ ▀ █"),
    "O": ("█▀█", "█▄█"),
    "S": ("█▀▀", "▄▄█"),
    " ": ("  ", "  "),
}

WORD = "SLACK CODE DEMOS"
PAD = 2  # columns left of the letters


def art(word: str = WORD) -> list[str]:
    """``word`` in block letters, one string per row."""
    return ["".join(FONT[ch][row] for ch in word) for row in range(ROWS)]


def small_art(word: str = WORD) -> list[str]:
    """``word`` in the two-row font, a column between letters."""
    return [" ".join(SMALL[ch][row] for ch in word) for row in range(2)]


def width(word: str = WORD) -> int:
    return len(art(word)[0])


def fits_big(columns: int) -> bool:
    return width() + PAD <= columns


class Banner(Widget):
    DEFAULT_CSS = "Banner { height: 7; width: 1fr; }"

    def on_resize(self, event: Resize) -> None:
        self.styles.height = 7 if fits_big(event.size.width) else 3

    def render(self) -> Text:
        room = self.size.width
        out = Text(no_wrap=True, overflow="crop")
        if fits_big(room):
            for row, line in enumerate(art()):
                solid = GRADIENT[row]
                shadow = mix(solid, BG, 0.45)
                out.append(" " * PAD)
                for ch in line:
                    out.append(ch, style=solid if ch == "█" else shadow)
                out.append("\n")
        else:
            for line in small_art():
                out.append(" " * PAD)
                for i, ch in enumerate(line):
                    out.append(ch, style=along(i / max(len(line) - 1, 1)))
                out.append("\n")
        rule = max(room - PAD, 1)
        out.append(" " * PAD)
        for i in range(rule):
            out.append("━", style=along(i / max(rule - 1, 1)))
        return out


# ---- the README copy: docs/banner.svg -------------------------------------------

CELL_W, CELL_H = 10, 20   # one terminal cell, in SVG units
_MARGIN_X, _MARGIN_Y = 2, 1   # cells of navy around the letters


def _shadow_path(ch: str, x: float, y: float) -> str:
    """A double box-drawing line (═ ║ ╔ ╗ ╚ ╝) in the cell at x, y, as path data."""
    w, h, d = CELL_W, CELL_H, 2.5   # d: each line's distance from the cell's middle
    cx, cy, r, b = x + w / 2, y + h / 2, x + w, y + h
    return {
        "═": f"M{x:g},{cy - d:g}H{r:g}M{x:g},{cy + d:g}H{r:g}",
        "║": f"M{cx - d:g},{y:g}V{b:g}M{cx + d:g},{y:g}V{b:g}",
        "╔": f"M{r:g},{cy - d:g}H{cx - d:g}V{b:g}M{r:g},{cy + d:g}H{cx + d:g}V{b:g}",
        "╗": f"M{x:g},{cy - d:g}H{cx + d:g}V{b:g}M{x:g},{cy + d:g}H{cx - d:g}V{b:g}",
        "╚": f"M{r:g},{cy + d:g}H{cx - d:g}V{y:g}M{r:g},{cy - d:g}H{cx + d:g}V{y:g}",
        "╝": f"M{x:g},{cy + d:g}H{cx + d:g}V{y:g}M{x:g},{cy - d:g}H{cx - d:g}V{y:g}",
    }[ch]


def svg(word: str = WORD) -> str:
    """The big banner as a standalone SVG: the same letters, row colours,
    shadow shade and rule as the app, on its navy. Blocks are rectangles and
    shadow lines are paths, so it looks the same whatever fonts the viewer has."""
    rows = art(word)
    cols = len(rows[0])
    w = (cols + 2 * _MARGIN_X) * CELL_W
    h = (ROWS + 1 + 2 * _MARGIN_Y) * CELL_H   # + 1: the rule's row
    x0, y0 = _MARGIN_X * CELL_W, _MARGIN_Y * CELL_H
    stops = "".join(f'<stop offset="{i / (len(GRADIENT) - 1):.3g}" stop-color="{c}"/>'
                    for i, c in enumerate(GRADIENT))
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}"'
        f' role="img" aria-label="{word.title()}">',
        f"<title>{word.title()}</title>",
        f'<defs><linearGradient id="rule" x1="0" x2="1">{stops}</linearGradient></defs>',
        f'<rect width="{w}" height="{h}" rx="14" fill="{BG}"/>',
    ]
    for row, line in enumerate(rows):
        solid = GRADIENT[row]
        y = y0 + row * CELL_H
        blocks, shadow, col = [], [], 0
        while col < cols:
            ch = line[col]
            if ch == "█":   # one rectangle per run of blocks
                end = col
                while end < cols and line[end] == "█":
                    end += 1
                # 0.5 taller than the cell, so stacked rows show no seam when scaled.
                blocks.append(f'<rect x="{x0 + col * CELL_W}" y="{y}" width="{(end - col) * CELL_W}"'
                              f' height="{CELL_H + 0.5:g}"/>')
                col = end
                continue
            if ch != " ":
                shadow.append(_shadow_path(ch, x0 + col * CELL_W, y))
            col += 1
        out.append(f'<g fill="{solid}">{"".join(blocks)}</g>')
        if shadow:
            out.append(f'<path d="{"".join(shadow)}" fill="none" stroke="{mix(solid, BG, 0.45)}"'
                       f' stroke-width="1.6"/>')
    rule_y = y0 + ROWS * CELL_H + CELL_H / 2 - 1.5
    out.append(f'<rect x="{x0}" y="{rule_y:g}" width="{cols * CELL_W}" height="3" fill="url(#rule)"/>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.stdout.write(svg())
