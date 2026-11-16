"""The weekly look for newer images (.github/scripts/check_versions.py)."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("check_versions", ROOT / ".github/scripts/check_versions.py")
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


class CheckVersionsTest(unittest.TestCase):
    def test_every_pinned_component_has_a_pattern_that_fits_its_tag(self):
        components = json.loads(V.VERSIONS.read_text())["components"]
        self.assertEqual(set(components), set(V.PATTERNS))
        for name, entry in components.items():
            self.assertRegex(entry["tag"], "^" + V.PATTERNS[name] + "$", name)

    def test_newest_takes_only_plain_releases_and_compares_numbers(self):
        tags = ["latest", "v3.7.9", "v3.7.18", "v3.10.0-rc1", "v3.7", "3.7.99", "v3.8.0"]
        self.assertEqual(V.newest(tags, V.PATTERNS["traefik"]), "v3.8.0")
        self.assertEqual(V.newest(["8.10.3-alpine", "8.12.0", "8.9.9-alpine"], V.PATTERNS["aiometadata-redis"]),
                         "8.10.3-alpine")
        self.assertIsNone(V.newest(["nightly"], V.PATTERNS["authelia"]))

    def test_changes_only_for_newer_tags_and_survive_a_dead_registry(self):
        components = {"traefik": {"image": "traefik", "tag": "v3.7.18"},
                      "authelia": {"image": "ghcr.io/authelia/authelia", "tag": "4.39.28"},
                      "prowlarr": {"image": "ghcr.io/hotio/prowlarr", "tag": "release-2.7.0.5723"}}

        def list_tags(image):
            if "authelia" in image:
                raise OSError("registry down")
            return {"traefik": ["v3.7.19", "v3.7.2"], "ghcr.io/hotio/prowlarr": ["release-2.7.0.5723", "nightly"]}[image]
        self.assertEqual(V.changes(components, list_tags), [("traefik", "v3.7.18", "v3.7.19")])

    def test_apply_changes_one_entry_and_marks_it_untested(self):
        text = V.VERSIONS.read_text()
        old = json.loads(text)["components"]["traefik"]["tag"]
        new = json.loads(V.apply(text, [("traefik", old, "v9.9.9")]))["components"]
        self.assertEqual((new["traefik"]["tag"], new["traefik"]["tested"]), ("v9.9.9", False))
        self.assertTrue(new["authelia"]["tested"])
        with self.assertRaises(ValueError):
            V.apply(text, [("traefik", "v0.0.0", "v9.9.9")])

    def test_registry_of(self):
        self.assertEqual(V.registry_of("traefik")[2], "library/traefik")
        self.assertEqual(V.registry_of("ghcr.io/hotio/prowlarr")[::2], ("https://ghcr.io", "hotio/prowlarr"))


if __name__ == "__main__":
    unittest.main()
