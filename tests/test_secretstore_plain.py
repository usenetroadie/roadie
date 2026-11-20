import tempfile
import unittest
from pathlib import Path

from roadie.secretstore import SecretStore


class PlainAlphabetTest(unittest.TestCase):
    def test_plain_alphabet_unambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = SecretStore(Path(tmp) / "s.json").generate("ntfy_topic", 200, "plain")
        self.assertFalse(set(value) & set("0O1lI"))
        self.assertTrue(value.isalnum())

    def test_existing_topic_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SecretStore(Path(tmp) / "s.json")
            store.set("ntfy_topic", "l0l0old")
            self.assertEqual(store.generate("ntfy_topic", 20, "plain"), "l0l0old")


if __name__ == "__main__":
    unittest.main()
