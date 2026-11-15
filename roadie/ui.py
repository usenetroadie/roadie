"""Terminal prompts: numbered lists and hidden input; works in any SSH terminal."""

import getpass
import os
import re
import select
import shutil
import sys
import termios
import textwrap
import threading
import time
from typing import Callable

from roadie.i18n import t

Validator = Callable[[str], "str | None"]
YES = {"y", "yes", "j", "ja"}
BULLET = re.compile(r"(?:- |\u2022 |\d+\. )")
CLEAR_SCREEN = "\033[H\033[2J\033[3J"  # home, visible screen, then scrollback (order like ncurses clear)
NO = {"n", "no", "nein"}


def terminal_columns(stream, fallback: int) -> int:
    """Width of the terminal `stream` writes to. shutil.get_terminal_size() prefers $COLUMNS,
    which can be a stale exported value (e.g. from a wider window) and then wins over the real size."""
    try:
        columns = os.get_terminal_size(stream.fileno()).columns
    except (AttributeError, ValueError, OSError):
        columns = 0
    return columns if columns > 0 else shutil.get_terminal_size((fallback, 24)).columns


class Prompter:
    def __init__(self, inp=None, out=None, secret_reader=getpass.getpass):
        self.inp = inp if inp is not None else sys.stdin
        self.out = out if out is not None else sys.stdout
        self.secret_reader = secret_reader
        self.tty = hasattr(self.out, "isatty") and self.out.isatty()
        self.color = self.tty and "NO_COLOR" not in os.environ
        self.width = 80
        self.width = self._terminal_columns()
        encoding = (getattr(self.out, "encoding", None) or "").lower().replace("-", "")
        self.unicode = encoding == "utf8" and os.environ.get("TERM") != "dumb"
        self._lock = threading.RLock()
        self._live = False  # a line without newline is on screen (terminal only)
        self._ticker: threading.Thread | None = None
        self._ticking = False
        self._step_label = ""
        self._step_title = ""
        self._step_started = time.monotonic()

    # output -----------------------------------------------------------
    def _clear_live(self) -> None:
        if self._live:
            self.out.write("\r\033[K")
            self._live = False

    def _print(self, text: str = "") -> None:
        self._stop_ticker()
        with self._lock:
            self._clear_live()
            print(text, file=self.out, flush=True)

    def status(self, text: str) -> None:
        """Rewrite one line in place (waiting times, progress); nothing when not on a terminal."""
        if not self.tty:
            return
        with self._lock:
            self.out.write("\r\033[K" + text[: max(10, self._width() - 1)])
            self.out.flush()
            self._live = True

    # step progress ----------------------------------------------------
    def step_begin(self, index: int, total: int, title: str) -> None:
        self._step_label = f"[{index}/{total}] {title}"
        self._step_title = title
        self._step_started = time.monotonic()
        if not self.tty:
            self._print(self._step_label)
            return
        self._ticking = True
        self._ticker = threading.Thread(target=self._tick, daemon=True)
        self._ticker.start()

    def _elapsed(self) -> str:
        secs = int(time.monotonic() - self._step_started)
        return f"{secs // 60}:{secs % 60:02d}"

    def _tick(self) -> None:
        frames = "|/-\\"
        n = 0
        while self._ticking:
            with self._lock:
                if self._ticking:
                    self.status(f"{self._step_label} {frames[n % 4]} {self._elapsed()}")
            n += 1
            time.sleep(0.25)

    def _stop_ticker(self) -> bool:
        """Stop the step animation; True when it was still running (nothing else printed)."""
        if not self._ticking:
            return False
        self._ticking = False
        if self._ticker and self._ticker is not threading.current_thread():
            self._ticker.join()
        return True

    def step_end(self, status: str = "ok", note: str | None = None) -> None:
        """Final line of a step: ok (with time), warn (something to look at later) or skipped."""
        self._stop_ticker()
        label = self._step_label + (f": {note}" if note else "")
        if status == "skipped":
            prefix = self._step_label[: len(self._step_label) - len(self._step_title)]
            self.warn(prefix + t("ui.step_skipped", title=self._step_title))
        elif status == "warn":
            self.warn(label)
        else:
            self.ok(f"{label} ({self._elapsed()})")

    def _paint(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def heading(self, msg: str) -> None:
        self._print()
        self._print(self._paint("1", msg))

    def alert(self, msg: str) -> None:
        """A heading that must not be missed (show-once secrets): bold red on a terminal."""
        self._print()
        self._print(self._paint("1;31", msg))

    def _terminal_columns(self) -> int:
        return terminal_columns(self.out, self.width)

    def _width(self) -> int:
        """Current terminal width (re-read on each call on a terminal; tests pin self.width)."""
        if self.tty:
            self.width = self._terminal_columns()
        return max(20, self.width)

    def _wrapped(self, msg: str, first: str = "", indent: str = "", paint: str = "") -> None:
        """Print msg wrapped at word boundaries; continuation lines align after `first`."""
        width = self._width() - 1
        for raw in msg.split("\n"):
            lead = raw[: len(raw) - len(raw.lstrip())]
            lines = textwrap.wrap(
                raw.strip(), width,
                initial_indent=(first or indent) + lead, subsequent_indent=(indent or " " * len(first)) + lead,
                break_long_words=False, break_on_hyphens=False,
            ) or [(first or indent).rstrip()]
            first = ""
            for line in lines:
                self._print(self._paint(paint, line) if paint else line)

    def _symbol(self, fancy: str, plain: str) -> str:
        return fancy + " " if self.unicode and self.color else plain

    def ok(self, msg: str) -> None:
        self._wrapped(msg, self._symbol("\u2713", "[ok] "), paint="32")

    def warn(self, msg: str) -> None:
        self._wrapped(msg, self._symbol("!", "[!]  "), paint="33")

    def fail(self, msg: str) -> None:
        self._wrapped(msg, self._symbol("\u2717", "[x]  "), paint="31")

    def info(self, msg: str) -> None:
        self._wrapped(msg)

    def hint(self, msg: str) -> None:
        self._wrapped(msg, indent="  ", paint="2")

    def copyable(self, text: str) -> None:
        """One line exactly as-is (URLs, secrets): never wrapped or indented; bold cyan on a terminal."""
        self._print(self._paint("1;36", text))

    def _fit(self, text: str, room: int) -> str:
        """Shorten a title to `room` characters (with an ellipsis) so a frame line never wraps."""
        if len(text) <= room:
            return text
        return text[: max(0, room - 1)] + ("\u2026" if self.unicode else ".")

    def section(self, index: int, total: int, title: str) -> None:
        bar, sep = ("\u2501", "\u00b7") if self.unicode else ("=", "-")
        width = self._width() - 1  # a full-width line makes some terminals add an empty line
        prefix = f"{bar * 2} {t('ui.section', index=index, total=total)} {sep} "
        head = prefix + self._fit(title, width - len(prefix) - 3) + " "
        self._print()
        self._print(self._paint("1", (head + bar * max(2, width - len(head)))[:width]))

    def box(self, title: str, lines: list[str]) -> None:
        """A bar on the left only (nothing to break on narrow screens); lines starting with
        "\\0" are copyable values on their own line."""
        bar = "\u2503 " if self.unicode else "| "
        inner = self._width() - 3  # bar + space, last column stays free
        for part in self._box_wrap(title, inner):
            self._print(bar + self._paint("1", part))
        for line in lines:
            if line.startswith("\0"):
                self.copyable(line.removeprefix("\0"))
                continue
            for part in self._box_wrap(line, inner):
                if len(part.strip()) > inner:  # one unbreakable token: keep it whole and copyable
                    self.copyable(part.strip())
                else:
                    self._print((bar + part).rstrip())
        self._print()

    @staticmethod
    def _box_wrap(line: str, width: int) -> list[str]:
        """Wrap one line; list items ("- ", "1. ") continue under their text, not their marker."""
        bullet = BULLET.match(line)
        hang = " " * len(bullet.group(0)) if bullet else ""
        return textwrap.wrap(line, width, subsequent_indent=hang,
                             break_long_words=False, break_on_hyphens=False) or [""]

    # input ------------------------------------------------------------
    def enter_pressed(self, timeout: float) -> bool:
        """Wait up to timeout seconds; True when Enter was pressed (terminal input only)."""
        if not (hasattr(self.inp, "isatty") and self.inp.isatty()):
            time.sleep(timeout)
            return False
        ready, _, _ = select.select([self.inp], [], [], timeout)
        if ready:
            self.inp.readline()
            return True
        return False

    def _readline(self, prompt: str) -> str:
        self._stop_ticker()
        with self._lock:
            self._clear_live()
        self.out.write(prompt)
        self.out.flush()
        line = self.inp.readline()
        if not line:
            raise EOFError("no more input")
        return line.strip()

    def _list(self, label: str, options, hints: list[str] | None = None) -> None:
        """Short lists one per line (optionally a hint sentence under each option); long ones in
        columns (filled top to bottom) so they fit on screen."""
        self._wrapped(label, first=self._q(""))  # "› " first, continuation lines under the text
        cells = [f"[{i}] {text}" for i, (_, text) in enumerate(options, 1)]
        if len(cells) <= 12:
            for i, cell in enumerate(cells):
                self._print(f"  {cell}")
                if hints and i < len(hints) and hints[i]:
                    self._wrapped(hints[i], indent="      ", paint="2")
            return
        cell_width = max(len(c) for c in cells) + 2
        columns = max(1, min(4, (self._width() - 2) // cell_width))
        rows = -(-len(cells) // columns)
        for r in range(rows):
            row = [cells[c * rows + r] for c in range(columns) if c * rows + r < len(cells)]
            self._print("  " + "".join(cell.ljust(cell_width) for cell in row).rstrip())

    def _q(self, label: str) -> str:
        return "\u203a " + label if self.unicode else label

    def choose(self, label: str, options: list, default: str | None = None,
               hints: list[str] | None = None) -> str:
        values = [v for v, _ in options]
        if default in values:
            label += " " + t("ui.enter_default", default=values.index(default) + 1)
        self._list(label, options, hints)
        while True:
            raw = self._readline("> ")
            if not raw and default in values:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return values[int(raw) - 1]
            self.fail(t("ui.invalid_choice", range=f"1-{len(options)}"))

    def choose_many(self, label: str, options: list, min_count: int = 1,
                    default: list[str] | None = None) -> list[str]:
        """`default`: current selection, kept with Enter."""
        self._list(label, options)
        self._print(t("ui.multi_hint"))
        names = dict(options)
        default = [v for v in default or [] if v in names]
        if default:
            self._print(t("ui.multi_keep", current=", ".join(names[v] for v in default)))
        values = [v for v, _ in options]
        while True:
            parts = self._readline("> ").replace(",", " ").split()
            if not parts and default:
                return list(default)
            if all(p.isdigit() and 1 <= int(p) <= len(options) for p in parts):
                picked = list(dict.fromkeys(values[int(p) - 1] for p in parts))
                if len(picked) >= min_count:
                    return picked
                self.fail(t("ui.too_few", count=min_count))
            else:
                self.fail(t("ui.invalid_choice", range=f"1-{len(options)}"))

    def ask(self, label: str, default: str | None = None, validate: Validator | None = None) -> str:
        suffix = " " + t("ui.enter_default", default=default) if default else ""
        while True:
            value = self._readline(f"{self._q(label)}{suffix}: ") or (default or "")
            error = validate(value) if validate else (None if value else t("ui.required"))
            if error is None:
                return value
            self.fail(error)

    def ask_secret(self, label: str, validate: Validator | None = None, keep: bool = False) -> str | None:
        """`keep`: there is a current value; empty input returns None = keep it (never shown)."""
        note = t("ui.hidden") + (", " + t("ui.keep_secret") if keep else "")
        while True:
            value = self.secret_reader(f"{label} ({note}): ").strip()
            if keep and not value:
                return None
            error = validate(value) if validate else (None if value else t("ui.required"))
            if error is None:
                return value
            self.fail(error)

    def confirm(self, label: str, default: bool = True) -> bool:
        hint = t("ui.yes_no_default_yes") if default else t("ui.yes_no_default_no")
        while True:
            raw = self._readline(f"{self._q(label)} {hint} ").lower()
            if not raw:
                return default
            if raw in YES:
                return True
            if raw in NO:
                return False
            self.fail(t("ui.yes_no_invalid"))

    # screen and timing --------------------------------------------------
    def offer_clear(self) -> None:
        """After a show-once secret: offer to wipe it from the screen (terminal only)."""
        if not self.tty:
            return
        if self.confirm(t("ui.clear_screen"), default=True):
            with self._lock:
                self.out.write(CLEAR_SCREEN)
                self.out.flush()

    def countdown(self, seconds: int, text_key: str, sleep=time.sleep) -> None:
        """Wait visibly (t(text_key, seconds=n) on the live line); keys pressed meanwhile are
        thrown away so they do not answer the next question."""
        saved = self._echo_off()
        try:
            for left in range(seconds, 0, -1):
                self.status(t(text_key, seconds=left))
                sleep(1)
        finally:
            if saved is not None:
                try:
                    termios.tcsetattr(self.inp, termios.TCSADRAIN, saved)
                except (termios.error, OSError, ValueError):
                    pass
        with self._lock:
            self._clear_live()
        self._discard_input()

    def _echo_off(self):
        """Hide keys typed during a wait (they would break the live line); returns the old settings."""
        if not (hasattr(self.inp, "isatty") and self.inp.isatty()):
            return None
        try:
            saved = termios.tcgetattr(self.inp)
            quiet = list(saved)
            quiet[3] &= ~termios.ECHO
            termios.tcsetattr(self.inp, termios.TCSADRAIN, quiet)
            return saved
        except (termios.error, OSError, ValueError):
            return None

    def _discard_input(self) -> None:
        if hasattr(self.inp, "isatty") and self.inp.isatty():
            try:
                termios.tcflush(self.inp, termios.TCIFLUSH)
            except (termios.error, OSError, ValueError):
                pass
