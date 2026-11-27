"""Release and install files: the safety settings must not get lost (security audit M3, L1, L11)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKFLOWS = ROOT / ".github/workflows"


class ReleaseFilesTest(unittest.TestCase):
    def test_wrapper_never_imports_from_current_folder(self):
        self.assertIn('exec python3 -P -m roadie "$@"', (ROOT / "install.sh").read_text())

    def test_installer_refuses_the_same_systems_as_preflight(self):
        from roadie.preflight import SYSTEMS
        line = re.search(r"^\s+(\"[^\n]+\"\)) ;;$", (ROOT / "install.sh").read_text(), re.M).group(1)
        self.assertEqual(line, "|".join(f'"{os_id} {version}"' for os_id, version in SYSTEMS) + ")")

    def test_signing_needs_the_release_environment(self):
        text = (WORKFLOWS / "release.yml").read_text()
        self.assertRegex(text, r"\n    environment: release\b")
        self.assertIn("secrets.RELEASE_SIGNING_KEY", text)

    def test_actions_pinned_and_without_stored_token(self):
        for wf in WORKFLOWS.glob("*.yml"):
            text = wf.read_text()
            uses = [u for u in re.findall(r"uses: (\S+)", text) if not u.startswith("./")]  # own workflows
            self.assertTrue(uses, wf.name)
            for action in uses:
                self.assertRegex(action, r"@[0-9a-f]{40}$", f"{wf.name}: {action}")
            checkouts = [u for u in uses if u.startswith("actions/checkout@")]  # the only one that stores a token
            self.assertEqual(text.count("persist-credentials: false"), len(checkouts), wf.name)

    def test_one_line_installer_trusts_the_release_key_only(self):
        from roadie.ops import selfupdate
        text = (ROOT / "get.sh").read_text()
        self.assertIn(f'RELEASE_KEY="{selfupdate.ALLOWED_SIGNERS}"', text)
        self.assertIn(f'REPO="{selfupdate.REPO}"', text)
        self.assertIn("ssh-keygen -Y verify", text)
        self.assertIn("sha256sum -c", text)
        self.assertLess(text.index("ssh-keygen -Y verify"), text.index("install.sh"))  # checked before use
        self.assertEqual(text.rstrip().splitlines()[-1], 'main "$@"')  # a cut-off download runs nothing
        for readme in ("README.md",):
            self.assertIn(f"raw.githubusercontent.com/{selfupdate.REPO}/main/get.sh | sudo bash",
                          (ROOT / readme).read_text())

    def test_english_only(self):
        self.assertFalse((ROOT / "README.de.md").exists())
        self.assertFalse((ROOT / "roadie/locales/de.json").exists())
        publish = ROOT / "tools/publish.sh"  # private only: the public repo has no tools/
        if publish.exists():
            self.assertNotIn("README.de.md", publish.read_text())

    def test_docs_scp_uses_the_key_and_no_german(self):
        import re
        publish = ROOT / "tools/publish.sh"  # private only; the public repo holds exactly the published files
        allow = (re.search(r"allow=\(([^)]*)\)", publish.read_text()).group(1).split() if publish.exists()
                 else ["README.md", "CONTRIBUTING.md", *(f"docs/{p.name}" for p in (ROOT / "docs").glob("*.md"))])
        for name in allow:
            path = ROOT / name
            files = [path] if path.is_file() else []
            for f in files:
                if f.suffix != ".md" or f.name == "manual-setup.md":  # by hand: the reader's own default key
                    continue
                text = f.read_text()
                self.assertNotIn("README.de", text, name)
                for line in text.splitlines():
                    if "scp " in line:
                        self.assertIn("-i ", line, (name, line))

    def test_release_settings_documented(self):
        note = (ROOT / ".github/RELEASING.md").read_text()
        for word in ("Required reviewers", "RELEASE_SIGNING_KEY", "v*"):
            self.assertIn(word, note)
