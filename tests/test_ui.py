import io
import termios
import unittest

from roadie import i18n
from roadie.ui import Prompter

OPTS = [("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")]


def make(text, secret="s3cr3t-value"):
    out = io.StringIO()
    return Prompter(inp=io.StringIO(text), out=out, secret_reader=lambda prompt: secret), out


class PrompterTest(unittest.TestCase):
    def test_choose_by_number(self):
        p, _ = make("2\n")
        self.assertEqual(p.choose("Pick", OPTS), "b")

    def test_choose_reasks_on_invalid(self):
        p, out = make("9\nx\n2\n")
        self.assertEqual(p.choose("Pick", OPTS), "b")
        self.assertEqual(out.getvalue().count("1-3"), 2)

    def test_choose_shows_hint_under_option(self):
        p, out = make("1\n")
        p.choose("Pick", OPTS, hints=["first letter", "", "third"])
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[lines.index("  [1] Alpha") + 1].strip(), "first letter")
        self.assertEqual(lines[lines.index("  [2] Beta") + 1], "  [3] Gamma")

    def test_choose_default_on_empty(self):
        p, _ = make("\n")
        self.assertEqual(p.choose("Pick", OPTS, default="c"), "c")

    def test_choose_many_keeps_order(self):
        p, _ = make("3,1\n")
        self.assertEqual(p.choose_many("Pick", OPTS), ["c", "a"])

    def test_choose_many_spaces_and_duplicates(self):
        p, _ = make("2 2  3\n")
        self.assertEqual(p.choose_many("Pick", OPTS), ["b", "c"])

    def test_choose_many_min_count(self):
        p, _ = make("\n1\n")
        self.assertEqual(p.choose_many("Pick", OPTS, min_count=1), ["a"])

    def test_ask_validate_reasks(self):
        p, out = make("bad\ngood\n")
        result = p.ask("Name", validate=lambda v: "nope" if v == "bad" else None)
        self.assertEqual(result, "good")
        self.assertIn("nope", out.getvalue())

    def test_ask_default(self):
        p, _ = make("\n")
        self.assertEqual(p.ask("Name", default="x"), "x")

    def test_ask_secret_not_echoed(self):
        p, out = make("")
        self.assertEqual(p.ask_secret("Key"), "s3cr3t-value")
        self.assertNotIn("s3cr3t-value", out.getvalue())

    def test_confirm_accepts_both_languages(self):
        for text, expected in [("j\n", True), ("y\n", True), ("ja\n", True), ("n\n", False), ("nein\n", False)]:
            p, _ = make(text)
            self.assertEqual(p.confirm("Ok?"), expected, text)

    def test_confirm_default(self):
        p, _ = make("\n")
        self.assertFalse(p.confirm("Ok?", default=False))

    def test_eof_raises(self):
        p, _ = make("")
        with self.assertRaises(EOFError):
            p.ask("Name")


class ColumnsTest(unittest.TestCase):
    def test_long_lists_use_columns_short_lists_do_not(self):
        long_opts = [(str(i), f"Name {i}") for i in range(1, 31)]
        p, out = make("1\n")
        p.width = 80
        p.choose("Pick", long_opts)
        lines = [l for l in out.getvalue().splitlines() if "[" in l and "Name" in l]
        self.assertLess(len(lines), 15)
        self.assertTrue(all(len(l) <= 80 for l in lines))
        self.assertIn("[30] Name 30", out.getvalue())
        p, out = make("1\n")
        p.choose("Pick", OPTS)
        self.assertEqual(len([l for l in out.getvalue().splitlines() if "[" in l]), 3)


class ColourTest(unittest.TestCase):
    def test_whole_line_coloured_on_terminal(self):
        p, out = make("")
        p.color = True
        p.unicode = False
        p.ok("fine")
        p.warn("careful")
        p.fail("broken")
        text = out.getvalue()
        self.assertIn("\033[32m[ok] fine\033[0m", text)
        self.assertIn("\033[33m[!]  careful\033[0m", text)
        self.assertIn("\033[31m[x]  broken\033[0m", text)

    def test_unicode_symbols_with_colour_and_unicode(self):
        p, out = make("")
        p.color = True
        p.unicode = True
        p.ok("fine")
        p.warn("careful")
        p.fail("broken")
        text = out.getvalue()
        self.assertIn("\033[32m\u2713 fine\033[0m", text)
        self.assertIn("\033[33m! careful\033[0m", text)
        self.assertIn("\033[31m\u2717 broken\033[0m", text)

    def test_no_colour_codes_when_not_a_terminal(self):
        p, out = make("")
        p.ok("fine")
        self.assertNotIn("\033", out.getvalue())


class LiveLineTest(unittest.TestCase):
    def test_live_line_only_on_terminal(self):
        p, out = make("")
        p.status("waiting 9:59")
        p.info("next")
        self.assertEqual(out.getvalue(), "next\n")

    def test_live_line_is_cleared_before_next_output(self):
        p, out = make("")
        p.tty = True
        p.status("waiting 9:59")
        p.info("next")
        self.assertIn("\r\033[Kwaiting 9:59", out.getvalue())
        self.assertTrue(out.getvalue().endswith("\r\033[Knext\n"))

    def test_enter_pressed_without_terminal_just_waits(self):
        p, _ = make("\n")
        self.assertFalse(p.enter_pressed(0))


class LayoutTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("de")
        self.addCleanup(i18n.set_language, "en")

    def test_wraps_at_word_boundary(self):
        p, out = make("")
        p.width = 30
        p.info("Dienste starten (dauert beim ersten Mal einige Minuten)")
        lines = out.getvalue().splitlines()
        self.assertTrue(all(len(l) <= 30 for l in lines), lines)
        self.assertIn("Minuten)", lines[-1] + lines[-2] if len(lines) > 1 else lines[0])
        self.assertTrue(any("Minuten" in l for l in lines))

    def test_wrapped_ok_aligns_after_symbol(self):
        p, out = make("")
        p.width = 30
        p.ok("alpha beta gamma delta epsilon zeta eta")
        lines = out.getvalue().splitlines()
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(l.startswith("     ") for l in lines[1:]), lines)

    def test_copyable_never_wrapped(self):
        p, out = make("")
        p.width = 40
        url = "https://aiostreams.example.duckdns.org/stremio/" + "x" * 150 + "/manifest.json"
        p.copyable(url)
        self.assertEqual(out.getvalue(), url + "\n")

    def test_section_header(self):
        p, out = make("")
        p.width = 40
        p.unicode = True
        p.section(3, 8, "Web-Adresse")
        line = [l for l in out.getvalue().splitlines() if l][0]
        self.assertTrue(line.startswith("\u2501\u2501 Teil 3 von 8 \u00b7 Web-Adresse"), line)
        self.assertEqual(len(line), 39)  # one column free: no extra line on full-width terminals

    def test_long_section_title_is_shortened(self):
        p, out = make("")
        p.width = 30
        p.unicode = True
        p.section(10, 10, "Ein sehr langer Abschnittstitel ohne Ende")
        line = [l for l in out.getvalue().splitlines() if l][0]
        self.assertLessEqual(len(line), 29)
        self.assertIn("\u2026", line)
        self.assertTrue(line.endswith("\u2501\u2501"), line)

    def test_box_lines_leave_last_column_free(self):
        p, out = make("")
        p.width = 40
        p.unicode = True
        p.box("Ein Titel, der viel zu lang für diesen schmalen Kasten ist", ["kurz", " ".join(["wort"] * 20)])
        lines = [l for l in out.getvalue().splitlines() if l]
        self.assertTrue(all(len(l) <= 39 for l in lines), lines)
        self.assertTrue(any(len(l) >= 35 for l in lines), lines)  # prose uses the width

    def test_choose_default_shown_as_enter(self):
        p, out = make("\n")
        self.assertEqual(p.choose("Wahl", OPTS, default="b"), "b")
        self.assertIn("(Enter = 2)", out.getvalue())

    def test_long_list_columns_follow_current_width(self):
        from unittest import mock
        p, out = make("1\n")
        p.width, p.tty = 40, True
        opts = [(str(i), f"Option {i}") for i in range(20)]
        with mock.patch("roadie.ui.shutil.get_terminal_size", return_value=mock.Mock(columns=100)):
            p.choose("Wahl", opts)
        rows = [l for l in out.getvalue().splitlines() if l.startswith("  [")]
        self.assertEqual(len(rows), 5)  # 4 columns at 100, not 2 at the stale 40

    def test_hint_indented_and_dim(self):
        p, out = make("")
        p.width = 30
        p.color = True
        p.hint("one two three four five six seven eight")
        lines = out.getvalue().splitlines()
        self.assertTrue(all(l.startswith("\033[2m  ") for l in lines), lines)

    def test_default_shown_as_enter(self):
        p, out = make("\n")
        self.assertEqual(p.ask("Verbindungen", default="20"), "20")
        self.assertIn("(Enter = 20)", out.getvalue())

    def test_confirm_hint_german(self):
        p, out = make("\n")
        self.assertFalse(p.confirm("Ok?", default=False))
        self.assertIn("(j/n, Enter = nein)", out.getvalue())

    def test_box_keeps_long_line_copyable(self):
        p, out = make("")
        p.width = 40
        p.unicode = True
        long = "https://\u2026" + "y" * 80
        p.box("Zug\u00e4nge", ["Login: admin", long])
        lines = out.getvalue().splitlines()
        self.assertIn(long, lines)
        self.assertTrue(all(len(l) <= 40 for l in lines if l != long), lines)
        self.assertTrue(lines[0].startswith("\u2503 ") and lines[-1] == "")

    def test_box_wraps_long_prose_inside_frame(self):
        p, out = make("")
        p.width = 40
        p.unicode = True
        sentence = " ".join(["word"] * 22)  # 109 chars
        p.box("T", [sentence])
        lines = [l for l in out.getvalue().splitlines() if l]
        self.assertTrue(all(len(l) <= 39 for l in lines), lines)
        self.assertGreater(len(lines), 3)
        self.assertEqual(" ".join(l[2:] for l in lines[1:]), sentence)

    def test_box_marker_line_is_copyable(self):
        p, out = make("")
        p.width = 40
        p.box("T", ["\0short", "plain"])
        lines = out.getvalue().splitlines()
        self.assertIn("short", lines)
        self.assertNotIn("\0", out.getvalue())

    def test_ascii_fallback(self):
        p, out = make("")
        p.width = 40
        p.unicode = False
        p.color = True
        p.section(1, 2, "X")
        p.box("T", ["a"])
        p.ok("fine")
        text = out.getvalue()
        for ch in "\u2501\u2503\u2713":
            self.assertNotIn(ch, text)
        self.assertIn("==", text)
        self.assertIn("| ", text)


class FakeTty(io.StringIO):
    """Terminal output with a fixed fd so the real terminal size can be faked."""
    def isatty(self):
        return True

    def fileno(self):
        return 99


class FakeTtyInput:
    """Terminal input whose already-typed lines can be thrown away by tcflush."""
    def __init__(self, lines):
        self.pending = list(lines)

    def isatty(self):
        return True

    def readline(self):
        return self.pending.pop(0) if self.pending else ""


def visible(line):
    import re
    return re.sub(r"\033\[[0-9;]*[A-Za-z]", "", line.split("\r")[-1])


class LookTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("de")
        self.addCleanup(i18n.set_language, "en")

    def test_box_left_bar_no_right_border(self):
        p, out = make("")
        p.width, p.unicode, p.color = 40, True, True
        p.box("Titel", ["kurz", " ".join(["wort"] * 20)])
        lines = out.getvalue().split("\n")
        self.assertEqual(lines[0], "┃ \033[1mTitel\033[0m")
        body = [l for l in lines[1:] if l]
        self.assertTrue(all(l.startswith("┃ ") for l in body), body)
        self.assertTrue(all(len(l) <= 39 and not l.endswith("┃") for l in body), body)
        self.assertNotIn("└", out.getvalue())
        self.assertTrue(out.getvalue().endswith("\n\n"))  # one blank line after the box

    def test_box_ascii_bar(self):
        p, out = make("")
        p.width, p.unicode = 40, False
        p.box("T", ["a"])
        self.assertEqual(out.getvalue(), "| T\n| a\n\n")

    def test_copyable_in_box_is_exact_line(self):
        p, out = make("")
        p.width = 40
        url = "https://aiostreams.example.duckdns.org/stremio/" + "x" * 60 + "/manifest.json"
        p.box("T", ["Adresse:", "\0" + url, "\0short"])
        lines = out.getvalue().splitlines()
        self.assertIn(url, lines)
        self.assertIn("short", lines)
        self.assertNotIn("\0", out.getvalue())

    def test_box_bullets_hanging_indent(self):
        p, out = make("")
        p.width, p.unicode = 40, True
        p.box("T", ["- " + " ".join(["eins"] * 15), "12. " + " ".join(["zwei"] * 15)])
        lines = out.getvalue().splitlines()[1:]
        dash = [l for l in lines if "eins" in l]
        num = [l for l in lines if "zwei" in l]
        self.assertTrue(dash[0].startswith("┃ - eins"))
        self.assertTrue(all(l.startswith("┃   eins") for l in dash[1:]), dash)
        self.assertTrue(all(l.startswith("┃     zwei") for l in num[1:]), num)
        self.assertTrue(all(len(l) <= 39 for l in lines))

    def test_copyable_bold_cyan_only_with_colour(self):
        p, out = make("")
        p.copyable("abc")
        self.assertEqual(out.getvalue(), "abc\n")
        p, out = make("")
        p.color = True
        p.copyable("abc")
        self.assertEqual(out.getvalue(), "\033[1;36mabc\033[0m\n")

    def test_alert_red_with_colour(self):
        p, out = make("")
        p.color = True
        p.alert("JETZT sichern")
        self.assertIn("\033[1;31mJETZT sichern\033[0m", out.getvalue())
        p, out = make("")
        p.alert("JETZT sichern")
        self.assertEqual(out.getvalue(), "\nJETZT sichern\n")

    def test_choose_default_in_label(self):
        p, out = make("\n")
        p.unicode = True
        self.assertEqual(p.choose("Wahl", OPTS, default="a"), "a")
        text = out.getvalue()
        self.assertEqual(text.split("\n")[0], "› Wahl (Enter = 1)")
        self.assertTrue(text.endswith("\n> "), repr(text))

    def test_long_choose_label_continues_under_text(self):
        p, out = make("1\n")
        p.width, p.unicode = 30, True
        p.choose("Wie soll die Qualität eingestellt sein?", OPTS, default="a")
        lines = out.getvalue().split("\n")
        self.assertTrue(lines[0].startswith("› Wie"), lines)
        self.assertTrue(lines[1].startswith("  ") and not lines[1].startswith("   "), lines)

    def test_offer_clear_noop_without_tty(self):
        p, out = make("j\n")
        p.offer_clear()
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(p.inp.readline(), "j\n")  # nothing asked, nothing read

    def test_offer_clear_clears_on_yes(self):
        out = FakeTty()
        p = Prompter(inp=io.StringIO("\n"), out=out)
        p.offer_clear()
        self.assertIn("Bildschirm jetzt leeren?", out.getvalue())
        self.assertTrue(out.getvalue().endswith("\033[H\033[2J\033[3J"))  # like ncurses clear

    def test_countdown_discards_early_input(self):
        from unittest import mock
        inp = FakeTtyInput(["1\n"])  # typed during the countdown
        out = FakeTty()
        p = Prompter(inp=inp, out=out)
        sleeps = []
        settings = [0, 0, 0, 0o777, 0, 0, []]
        with mock.patch("roadie.ui.termios.tcflush", side_effect=lambda f, q: f.pending.clear()) as flush, \
                mock.patch("roadie.ui.termios.tcgetattr", return_value=settings), \
                mock.patch("roadie.ui.termios.tcsetattr") as setattr_, \
                mock.patch("roadie.ui.os.get_terminal_size", return_value=mock.Mock(columns=53)):
            p.countdown(3, "ui.clear_screen", sleep=sleeps.append)
        flush.assert_called_once()
        quiet, restored = setattr_.call_args_list[0][0][2], setattr_.call_args_list[1][0][2]
        self.assertFalse(quiet[3] & termios.ECHO)  # echo off while waiting
        self.assertIs(restored, settings)  # old settings back afterwards
        self.assertEqual(sum(sleeps), 3)
        inp.pending.append("2\n")
        self.assertEqual(p.choose("Wahl", OPTS, default="a"), "b")

    def test_step_end_statuses(self):
        p, out = make("")
        p.unicode = False
        p.step_begin(2, 5, "Push")
        p.step_end("ok", "Test gesendet")
        p.step_begin(3, 5, "Backup")
        p.step_end("warn", "kein Schlüssel")
        p.step_begin(4, 5, "Wartung")
        p.step_end("skipped")
        lines = out.getvalue().splitlines()
        self.assertRegex(lines[1], r"^\[ok\] \[2/5\] Push: Test gesendet \(0:0\d\)$")
        self.assertEqual(lines[3], "[!]  [3/5] Backup: kein Schlüssel")
        self.assertEqual(lines[5], "[!]  [4/5] Wartung: übersprungen")

    def test_wraps_during_step(self):
        import os
        from unittest import mock
        out = FakeTty()
        p = Prompter(inp=io.StringIO(""), out=out)
        text = "Installiere die kostenlose App ntfy aus dem App Store oder von Google Play und tippe dort auf Plus."
        with mock.patch.dict(os.environ, {"COLUMNS": "100", "LINES": "30"}), \
                mock.patch("roadie.ui.os.get_terminal_size", return_value=mock.Mock(columns=53)):
            p.step_begin(1, 2, "Push")
            p.status("ticker frame")  # what the ticker draws
            p.info(text)
            p.hint(text)
            p.step_end()
        lines = [visible(l) for l in out.getvalue().split("\n")]
        self.assertTrue(all(len(l) <= 53 for l in lines), lines)
        self.assertGreater(len(lines), 5)
