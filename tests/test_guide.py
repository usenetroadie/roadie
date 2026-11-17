"""docs/manual-setup.md builds the same server by hand: its files must be the ones roadie writes."""

import os
import unittest

from tests import guide_files


class GuideTest(unittest.TestCase):
    def test_guide_files_match_what_roadie_writes(self):
        text = guide_files.GUIDE.read_text()
        if os.environ.get("ROADIE_UPDATE_GUIDE"):
            text = guide_files.update(text)
            guide_files.GUIDE.write_text(text)
        shown, made = guide_files.blocks(text), guide_files.files()
        self.assertEqual(sorted(shown), sorted(made), "every file once in the guide")
        for name, content in made.items():
            self.assertEqual(shown[name], content,
                             f"{name} differs from roadie – run: ROADIE_UPDATE_GUIDE=1 python3 -m unittest tests.test_guide")

    def test_no_secret_left_in_the_examples(self):
        text = guide_files.GUIDE.read_text()
        for word in ("example-password", "EXAMPLEINDEXERKEY", "change-me-to-something-long"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
