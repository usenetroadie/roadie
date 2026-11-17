"""The start screen: the roadcase logo as pixel art next to the word "roadie".

Drawn with half blocks (two pixels per character cell) in 256 colours, so it works in any
modern SSH terminal. Narrow windows (phones) get the word only, plain output one text line."""

from roadie import __version__
from roadie.i18n import t

# One character per pixel, "." = empty. Same shapes and colours as assets/logo.svg.
LOGO = (
    ".......mmmmmmmm.......",
    ".......m......m.......",
    ".llleeeeeeeeeeeeeelll.",
    ".lllddddddddddddddlll.",
    ".lllddddmmddmmddddlll.",
    ".eddddddmmddmmdddddde.",
    ".ebbbbbbmmbbmmbbbbbbe.",
    ".edddddddddddddddddde.",
    ".edddggddddddddddddde.",
    ".eddddggdddddddddddde.",
    ".edddddggddddddddddde.",
    ".edddddggddddddddddde.",
    ".eddddggdddggggggddde.",
    ".edddggddddggggggddde.",
    ".edddddddddddddddddde.",
    ".lllddddddddddddddlll.",
    ".lllddddddddddddddlll.",
    ".llleeeeeeeeeeeeeelll.",
)
LOGO_COLOURS = {"l": 252, "m": 247, "e": 240, "d": 236, "b": 238, "g": 71}
GREEN, TEXT, DIM = 71, 250, 240
FONT = {  # 8 pixels high: 3 for ascenders, 5 for the letters
    "r": ("......", "......", "......", "##.###", "####..", "##....", "##....", "##...."),
    "o": ("......", "......", "......", ".####.", "##..##", "##..##", "##..##", ".####."),
    "a": ("......", "......", "......", ".####.", "....##", ".#####", "##..##", ".#####"),
    "d": ("....##", "....##", "....##", ".#####", "##..##", "##..##", "##..##", ".#####"),
    "i": ("##", "..", "..", "##", "##", "##", "##", "##"),
    "e": ("......", "......", "......", ".####.", "##..##", "######", "##....", ".####."),
}
WORD = tuple("..".join(FONT[c][i] for c in "roadie") for i in range(8))
GAP = 4  # between logo and text
FULL_WIDTH = len(LOGO[0]) + GAP + len(WORD[0]) + 2


def half_blocks(pixels, colour) -> list[str]:
    """Pixel rows -> terminal lines, two pixel rows per line. colour(char) -> 256-colour or None."""
    width = max(map(len, pixels))
    rows = [r.ljust(width, ".") for r in pixels]
    if len(rows) % 2:
        rows.append("." * width)
    out = []
    for y in range(0, len(rows), 2):
        line = ""
        for top, bottom in zip(rows[y], rows[y + 1]):
            a, b = colour(top), colour(bottom)
            if a is None and b is None:
                line += " "
            elif b is None:
                line += f"\033[38;5;{a}m▀\033[0m"
            elif a is None:
                line += f"\033[38;5;{b}m▄\033[0m"
            elif a == b:
                line += f"\033[38;5;{a}m█\033[0m"
            else:
                line += f"\033[38;5;{a};48;5;{b}m▀\033[0m"
        out.append(line)
    return out


def _fg(code: int, text: str) -> str:
    return f"\033[38;5;{code}m{text}\033[0m"


def lines(columns: int, fancy: bool) -> list[str]:
    """fancy: a colour terminal with Unicode. Without it, or very narrow: one plain line."""
    slogan = t("banner.slogan")
    if not fancy or columns < len(WORD[0]) + 4:
        return [f"roadie {__version__} – {slogan}"]
    word = half_blocks(WORD, lambda c: GREEN if c == "#" else None)
    text = [""] + word + ["", _fg(TEXT, slogan), "", _fg(DIM, f"v{__version__}")]
    if columns < FULL_WIDTH:  # phones: the word and the slogan
        return [""] + ["  " + line for line in text[1:]] + [""]
    logo = half_blocks(LOGO, LOGO_COLOURS.get)
    return [""] + ["  " + art + " " * GAP + side for art, side in zip(logo, text + [""] * len(logo))] + [""]


def show(ui) -> None:
    """At the start of an interactive run; nothing when the output is no terminal."""
    if not ui.tty:
        return
    for line in lines(ui._width(), ui.color and ui.unicode):
        ui.raw(line)
