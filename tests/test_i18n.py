import re
import unittest

from roadie import i18n


PEOPLE_MENU = {"people.add", "people.change", "people.remove", "people.url", "people.back"}

# labels of other programs' pages (indexer account, AIOStreams), not of the wizard
OUTSIDE_LABELS = {"API", "Profile", "Profil", "Custom", "Add"}  # "Add": a button on the Authelia login page


class I18nTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_only_english(self):
        self.assertEqual(i18n.LANGUAGES, ("en",))

    def test_missing_key_falls_back_to_english(self):
        i18n._catalogs["en"]["only.en"] = "english only"
        try:
            self.assertEqual(i18n.t("only.en"), "english only")
        finally:
            del i18n._catalogs["en"]["only.en"]

    def test_unknown_key_returns_key(self):
        self.assertEqual(i18n.t("no.such"), "no.such")

    def test_english_has_no_german_quotes_or_words(self):
        tells = ["„", "“", "ä", "ö", "ü", "ß", " und ", " oder ", " nicht ", " ist ", " der ",
                 " die ", " das ", " mit ", " bitte ", "Einstellungen", "Hilfe", "Dienste",
                 "wähle", "Schlüssel", "z. B."]
        for key, text in i18n.load("en").items():
            for word in tells:
                self.assertNotIn(word, text.lower() if word.islower() else text, key)

    def test_menu_paths_name_existing_labels(self):
        """Texts point to menu items and help pages ("Help → Download a backup",
        "sudo roadie → 'Manage people'", "choose 'Restart services'"). Each target must be
        a real label in the same language, so renaming a label cannot leave stale hints."""
        quote = r"['„]([^'“]+)['“]"
        for lang in ("en",):
            cat = i18n.load(lang)
            help_titles = {v for k, v in cat.items() if k.startswith("help.") and k.endswith(".title")}
            menu = {v for k, v in cat.items()
                    if k.startswith(("menu.", "cli.menu.")) or k in PEOPLE_MENU}
            for key, text in cat.items():
                for m in re.finditer(r"(?:Help|Hilfe) → (?:" + quote + r"|([^.(,\n]+))", text):
                    self.assertIn((m.group(1) or m.group(2)).strip(), help_titles, (lang, key))
                for m in re.finditer(r"roadie → (?:" + quote + r"|([^.(,\n]+))", text):
                    self.assertIn((m.group(1) or m.group(2)).strip(), menu, (lang, key))
                pick = r"(?i)(?:choose|wähle|under|unter|then|dann)(?: in sudo roadie)? "
                for m in re.finditer(pick + quote, text):
                    if m.group(1) not in OUTSIDE_LABELS and not m.group(1).startswith("sudo "):
                        self.assertIn(m.group(1), menu | help_titles, (lang, key))

    def test_unknown_language_rejected(self):
        with self.assertRaises(ValueError):
            i18n.set_language("fr")
