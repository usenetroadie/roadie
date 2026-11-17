"""The start screen: logo and word on wide terminals, the word on phones, one line otherwise."""

import io
import re
import unittest

from roadie import __version__, banner, i18n
from roadie.ui import Prompter

ANSI = re.compile(r"\033\[[0-9;]*m")


def visible(line: str) -> str:
    return ANSI.sub("", line)


class BannerTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")

    def test_pixel_rows_line_up(self):
        self.assertEqual({len(r) for r in banner.LOGO}, {22})
        self.assertTrue(set("".join(banner.LOGO)) <= set(banner.LOGO_COLOURS) | {"."})
        for letter in banner.FONT.values():
            self.assertEqual(len(letter), 8)
            self.assertEqual(len({len(r) for r in letter}), 1)

    def test_wide_terminal_shows_logo_word_slogan_and_version(self):
        out = banner.lines(100, True)
        self.assertEqual(len(out), 11)  # empty line, 9 rows of art, empty line
        text = "\n".join(visible(line) for line in out)
        self.assertIn(i18n.t("banner.slogan"), text)
        self.assertIn(f"v{__version__}", text)
        self.assertIn("\033[38;5;71m", "".join(out))  # the green word
        self.assertLessEqual(max(len(visible(line)) for line in out), banner.FULL_WIDTH)

    def test_phone_gets_the_word_only(self):
        out = banner.lines(50, True)
        self.assertLessEqual(max(len(visible(line)) for line in out), 50)
        self.assertNotIn("\033[38;5;236m", "".join(out))  # no case body
        self.assertIn(i18n.t("banner.slogan"), "\n".join(map(visible, out)))

    def test_plain_line_without_colour_or_room(self):
        for out in (banner.lines(120, False), banner.lines(30, True)):
            self.assertEqual(out, [f"roadie {__version__} – {i18n.t('banner.slogan')}"])

    def test_nothing_when_output_is_no_terminal(self):
        buf = io.StringIO()
        banner.show(Prompter(inp=io.StringIO(""), out=buf))
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
