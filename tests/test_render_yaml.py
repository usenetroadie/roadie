import json
import unittest

from roadie.render.yaml import dump

try:
    import yaml
except ImportError:  # PyYAML is optional, only used to double-check the emitter
    yaml = None

SAMPLE = {
    "services": {"a": {"image": "x:1", "ports": ["0.0.0.0:80:80"], "environment": {"TZ": "UTC"},
                       "command": ["--a=b", "it's \"quoted\""], "healthcheck": {"interval": "30s"}}},
    "empty_list": [], "empty_map": {}, "bool": True, "num": 3, "none": None,
    "nested": [{"k": "v", "l": [1, 2]}, "plain"], "weird key:": "x",
}


class YamlTest(unittest.TestCase):
    def test_strings_are_quoted(self):
        self.assertIn('image: "x:1"', dump(SAMPLE))

    def test_scalars(self):
        out = dump(SAMPLE)
        self.assertIn("bool: true", out)
        self.assertIn("num: 3", out)
        self.assertIn("none: null", out)
        self.assertIn("empty_list: []", out)
        self.assertIn("empty_map: {}", out)

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_roundtrip_with_pyyaml(self):
        self.assertEqual(yaml.safe_load(dump(SAMPLE)), json.loads(json.dumps(SAMPLE)))


class YamlKeyTest(unittest.TestCase):
    SPECIAL = ["null", "Null", "true", "False", "yes", "No", "on", "OFF", "y", "n", "~", "123", "1e3", "0x1f", ".5"]

    def test_special_words_are_quoted_keys(self):
        out = dump({"users": {k: {"x": 1} for k in self.SPECIAL}})
        for k in self.SPECIAL:
            self.assertIn(f"  {json.dumps(k)}:", out, k)
        self.assertIn("  plainname: 1\n", dump({"users": {"plainname": 1}}))

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_special_keys_roundtrip(self):
        data = {"users": {k: {"disabled": False} for k in self.SPECIAL + ["tim.b", "a-b_c"]}}
        self.assertEqual(yaml.safe_load(dump(data)), data)
