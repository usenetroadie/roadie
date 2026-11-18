import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie.ops import selfupdate
from tests.helpers import MockHttp

HAVE_SSH_KEYGEN = shutil.which("ssh-keygen") is not None


def make_archive(path: Path, tag: str, printed_version: str, extra=None) -> Path:
    """Fake release archive: <prefix>/roadie/ whose --version prints `printed_version`."""
    prefix = f"roadie-{tag}"
    files = {
        f"{prefix}/install.sh": "#!/bin/sh\n",
        f"{prefix}/roadie/__init__.py": f'__version__ = "{printed_version}"\n',
        f"{prefix}/roadie/__main__.py": (
            "import sys\nfrom roadie import __version__\n"
            "if '--version' in sys.argv:\n    print(f'roadie {__version__}')\n    sys.exit(0)\n"
            "sys.exit(3)\n"),
    }
    with tarfile.open(path, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    return path


def sha_line(path: Path) -> str:
    return f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.app = self.tmp / "opt/roadie/app"
        (self.app / "roadie").mkdir(parents=True)
        (self.app / "roadie/__init__.py").write_text('__version__ = "0.1.0"\n')


@unittest.skipUnless(HAVE_SSH_KEYGEN, "ssh-keygen missing")
class SignedBase(Base):
    def setUp(self):
        super().setUp()
        keydir = self.tmp / "keys"
        keydir.mkdir()
        self.key = keydir / "k"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(self.key)], check=True)
        self.pub = (keydir / "k.pub").read_text().strip()
        other = keydir / "other"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(other)], check=True)
        self.other_key = other

    def sign(self, archive: Path, key: Path | None = None) -> bytes:
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(key or self.key), "-n",
                        "roadie-release", str(archive)], check=True, capture_output=True)
        sig = Path(str(archive) + ".sig")
        data = sig.read_bytes()
        sig.unlink()
        return data


class VerifyTest(SignedBase):
    def test_verify_good_signature(self):
        a = make_archive(self.tmp / "roadie-v0.2.0.tar.gz", "v0.2.0", "0.2.0")
        self.assertTrue(selfupdate.verify(a, sha_line(a), self.sign(a), self.pub))

    def test_signature_from_other_key_rejected(self):
        a = make_archive(self.tmp / "roadie-v0.2.0.tar.gz", "v0.2.0", "0.2.0")
        self.assertFalse(selfupdate.verify(a, sha_line(a), self.sign(a, self.other_key), self.pub))

    def test_bad_checksum_rejected(self):
        a = make_archive(self.tmp / "roadie-v0.2.0.tar.gz", "v0.2.0", "0.2.0")
        self.assertFalse(selfupdate.verify(a, "0" * 64 + "  x\n", self.sign(a), self.pub))
        self.assertFalse(selfupdate.verify(a, "", self.sign(a), self.pub))

    def test_tampered_archive_rejected(self):
        a = make_archive(self.tmp / "roadie-v0.2.0.tar.gz", "v0.2.0", "0.2.0")
        sig = self.sign(a)
        a.write_bytes(a.read_bytes() + b"\0")
        self.assertFalse(selfupdate.verify(a, sha_line(a), sig, self.pub))  # checksum fits, signature not


class InstallTest(Base):
    def test_install_swaps_and_keeps_prev(self):
        a = make_archive(self.tmp / "a.tar.gz", "v0.2.0", "0.2.0")
        self.assertEqual(selfupdate.install(a, self.app, "v0.2.0"), "ok")
        self.assertIn("0.2.0", (self.app / "roadie/__init__.py").read_text())
        self.assertIn("0.1.0", (self.app.parent / "app.prev/roadie/__init__.py").read_text())
        self.assertFalse((self.app.parent / "app.new").exists())
        self.assertFalse((self.app / "install.sh").exists())  # only the package is installed

    def test_older_prev_is_replaced(self):
        (self.app.parent / "app.prev/roadie").mkdir(parents=True)
        (self.app.parent / "app.prev/old-marker").write_text("x")
        a = make_archive(self.tmp / "a.tar.gz", "v0.2.0", "0.2.0")
        self.assertEqual(selfupdate.install(a, self.app, "v0.2.0"), "ok")
        self.assertFalse((self.app.parent / "app.prev/old-marker").exists())

    def test_failed_new_version_rolls_back(self):
        a = make_archive(self.tmp / "a.tar.gz", "v0.2.0", "0.1.9")  # --version prints the wrong version
        self.assertEqual(selfupdate.install(a, self.app, "v0.2.0"), "failed")
        self.assertIn("0.1.0", (self.app / "roadie/__init__.py").read_text())
        self.assertFalse((self.app.parent / "app.new").exists())
        self.assertFalse((self.app.parent / "app.prev").exists())

    def test_crashing_new_version_rolls_back(self):
        a = self.tmp / "a.tar.gz"
        with tarfile.open(a, "w:gz") as tar:
            data = b"raise SystemExit(1)\n"
            for name in ("roadie-v0.2.0/roadie/__init__.py", "roadie-v0.2.0/roadie/__main__.py"):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.assertEqual(selfupdate.install(a, self.app, "v0.2.0"), "failed")
        self.assertIn("0.1.0", (self.app / "roadie/__init__.py").read_text())

    def test_unsafe_tar_member_rejected(self):
        cases = []
        evil = tarfile.TarInfo("roadie-v0.2.0/roadie/../../../evil")
        evil.size = 1
        cases.append(evil)
        link = tarfile.TarInfo("roadie-v0.2.0/roadie/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        cases.append(link)
        hard = tarfile.TarInfo("roadie-v0.2.0/roadie/hard")
        hard.type, hard.linkname = tarfile.LNKTYPE, "../../../etc/passwd"
        cases.append(hard)
        fifo = tarfile.TarInfo("roadie-v0.2.0/roadie/fifo")
        fifo.type = tarfile.FIFOTYPE
        cases.append(fifo)
        for member in cases:
            with self.subTest(member=member.name):
                a = make_archive(self.tmp / "a.tar.gz", "v0.2.0", "0.2.0", extra=[member])
                self.assertEqual(selfupdate.install(a, self.app, "v0.2.0"), "unsafe")
                self.assertIn("0.1.0", (self.app / "roadie/__init__.py").read_text())
                self.assertFalse((self.app.parent / "app.new").exists())

    def test_members_outside_the_package_are_ignored(self):
        extra = []
        for name in ("/tmp/evil-roadie-test", "roadie-v0.2.0/../../evil-roadie-test"):
            info = tarfile.TarInfo(name)
            info.size = 1
            extra.append(info)
        link = tarfile.TarInfo("roadie-v0.2.0/docs-link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        extra.append(link)
        a = make_archive(self.tmp / "a.tar.gz", "v0.2.0", "0.2.0", extra=extra)
        self.assertEqual(selfupdate.install(a, self.app, "v0.2.0"), "ok")
        self.assertFalse(Path("/tmp/evil-roadie-test").exists())
        self.assertEqual(sorted(p.name for p in self.app.iterdir()), ["roadie"])

    def test_wrong_prefix_rejected(self):
        a = make_archive(self.tmp / "a.tar.gz", "v0.3.0", "0.2.0")
        self.assertEqual(selfupdate.install(a, self.app, "v0.2.0"), "unsafe")

    def test_failed_swap_is_rolled_back(self):
        a = make_archive(self.tmp / "a.tar.gz", "v0.2.0", "0.2.0")
        real_rename = selfupdate.os.rename

        def rename(src, dst):
            if Path(src).name == "app.new":
                raise OSError("disk trouble")
            return real_rename(src, dst)

        with mock.patch.object(selfupdate.os, "rename", side_effect=rename):
            self.assertEqual(selfupdate.install(a, self.app, "v0.2.0"), "failed")
        self.assertIn("0.1.0", (self.app / "roadie/__init__.py").read_text())
        self.assertFalse((self.app.parent / "app.new").exists())


class VersionTest(unittest.TestCase):
    def test_version_comparison(self):
        newer = selfupdate.is_newer
        self.assertTrue(newer("v0.2.0", "0.1.0"))
        self.assertTrue(newer("v0.10.0", "0.9.9"))
        self.assertTrue(newer("v1.0.0", "0.99.99"))
        self.assertTrue(newer("v0.1.0", "0.1.0.dev0"))  # dev builds are older than the release
        self.assertFalse(newer("v0.1.0", "0.1.0"))
        self.assertFalse(newer("v0.1.0", "0.2.0"))
        self.assertFalse(newer("v0.0.9", "0.1.0.dev0"))
        self.assertFalse(newer("latest", "0.1.0"))
        self.assertFalse(newer("v0.2.0-rc1", "0.1.0"))  # only vX.Y.Z tags count


class FakeUi:
    def __init__(self):
        self.lines = []

    def __getattr__(self, name):
        return lambda text, *a, **k: self.lines.append((name, text))

    def text(self):
        return "\n".join(t for _, t in self.lines)


class RunTest(Base):
    def setUp(self):
        super().setUp()
        self.paths = mock.Mock(root=self.tmp)
        self.ui = FakeUi()

    def test_disabled_without_repo(self):
        with mock.patch.object(selfupdate, "REPO", ""), mock.patch.object(selfupdate, "ALLOWED_SIGNERS", "x"), \
                mock.patch.object(selfupdate, "latest") as latest, mock.patch("os.execv") as execv:
            self.assertIsNone(selfupdate.run(self.paths, self.ui, []))
        latest.assert_not_called()
        execv.assert_not_called()
        self.assertIn(("info", selfupdate.t("selfupdate.disabled")), self.ui.lines)
        self.assertEqual(selfupdate.REPO, "usenetroadie/roadie")  # the shipped version updates itself
        self.assertTrue(selfupdate.enabled())

    def test_disabled_without_key(self):
        with mock.patch.object(selfupdate, "REPO", "owner/roadie"), \
                mock.patch.object(selfupdate, "ALLOWED_SIGNERS", ""):
            self.assertFalse(selfupdate.enabled())
            self.assertIsNone(selfupdate.run(self.paths, self.ui, []))

    def test_skipped_when_not_installed_as_app(self):
        shutil.rmtree(self.app)
        with mock.patch.object(selfupdate, "REPO", "o/r"), mock.patch.object(selfupdate, "ALLOWED_SIGNERS", "x"), \
                mock.patch.object(selfupdate, "latest") as latest:
            self.assertIsNone(selfupdate.run(self.paths, self.ui, []))
        latest.assert_not_called()


@unittest.skipUnless(HAVE_SSH_KEYGEN, "ssh-keygen missing")
class RunWithServerTest(SignedBase):
    def setUp(self):
        super().setUp()
        self.paths = mock.Mock(root=self.tmp)
        self.ui = FakeUi()
        self.archive = make_archive(self.tmp / "roadie-v0.2.0.tar.gz", "v0.2.0", "0.2.0")
        self.files = {"roadie-v0.2.0.tar.gz": self.archive.read_bytes(),
                      "roadie-v0.2.0.tar.gz.sha256": sha_line(self.archive).encode(),
                      "roadie-v0.2.0.tar.gz.sig": self.sign(self.archive)}
        routes = {"/repos/o/r/releases/latest": lambda q, h: (200, json.dumps({"tag_name": "v0.2.0"}),
                                                              "application/json")}
        for name in self.files:
            routes[f"/o/r/releases/download/v0.2.0/{name}"] = (
                lambda n: lambda q, h: (200, self.files[n]) if n in self.files else (404, ""))(name)
        self.server = MockHttp(routes)
        self.addCleanup(self.server.close)
        for patch in (mock.patch.object(selfupdate, "REPO", "o/r"),
                      mock.patch.object(selfupdate, "ALLOWED_SIGNERS", self.pub),
                      mock.patch.object(selfupdate, "API_URL", self.server.url),
                      mock.patch.object(selfupdate, "DOWNLOAD_URL", self.server.url),
                      mock.patch.object(selfupdate, "RAW_URL", self.server.url),  # never the real GitHub
                      mock.patch.object(selfupdate, "__version__", "0.1.0")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_latest(self):
        self.assertEqual(selfupdate.latest("o/r"), "v0.2.0")
        self.assertIsNone(selfupdate.latest("o/missing"))

    def test_latest_without_the_api(self):
        """GitHub's API refuses (rate limit on a shared address): the release page still tells."""
        with mock.patch.object(selfupdate, "_get", side_effect=OSError("403")), \
                mock.patch.object(selfupdate, "_final_url",
                                  return_value="https://github.com/o/r/releases/tag/v0.2.0") as final:
            self.assertEqual(selfupdate.latest("o/r"), "v0.2.0")
            final.return_value = "https://github.com/o/r/releases"  # no release yet
            self.assertIsNone(selfupdate.latest("o/r"))

    def test_incomplete_read_is_no_crash(self):
        import http.client
        with mock.patch.object(selfupdate, "_get", side_effect=http.client.IncompleteRead(b"x")):
            self.assertIsNone(selfupdate.latest("o/r"))
            self.assertIsNone(selfupdate.run(self.paths, self.ui, []))
            self.assertIsNone(selfupdate.notice({}, now=1.0))

    def test_restart_failure_is_explained(self):
        with mock.patch("os.execv", side_effect=OSError("no wrapper")):
            self.assertEqual(selfupdate.run(self.paths, self.ui, []), 1)
        self.assertIn(selfupdate.t("selfupdate.restart_failed"), self.ui.text())

    def test_unsafe_archive_has_own_message(self):
        with mock.patch.object(selfupdate, "verify", return_value=True), \
                mock.patch.object(selfupdate, "install", return_value="unsafe"), mock.patch("os.execv") as execv:
            self.assertIsNone(selfupdate.run(self.paths, self.ui, []))
        execv.assert_not_called()
        self.assertIn(selfupdate.t("selfupdate.unsafe"), self.ui.text())

    def test_good_signature_installs_and_reexecs(self):
        with mock.patch("os.execv") as execv:
            selfupdate.run(self.paths, self.ui, ["--lang", "de"])
        self.assertIn("0.2.0", (self.app / "roadie/__init__.py").read_text())
        wrapper, args = execv.call_args[0]
        self.assertEqual(args[1:], [selfupdate.SKIP_FLAG, "--lang", "de", "update"])
        self.assertEqual(args[0], wrapper)

    def test_bad_signature_keeps_old(self):
        self.files["roadie-v0.2.0.tar.gz.sig"] = self.sign(self.archive, self.other_key)
        with mock.patch("os.execv") as execv:
            self.assertIsNone(selfupdate.run(self.paths, self.ui, []))
        execv.assert_not_called()
        self.assertIn("0.1.0", (self.app / "roadie/__init__.py").read_text())
        self.assertIn(selfupdate.t("selfupdate.untrusted"), self.ui.text())

    def test_missing_signature_keeps_old(self):
        del self.files["roadie-v0.2.0.tar.gz.sig"]
        with mock.patch("os.execv") as execv:
            self.assertIsNone(selfupdate.run(self.paths, self.ui, []))
        execv.assert_not_called()
        self.assertIn("0.1.0", (self.app / "roadie/__init__.py").read_text())
        self.assertIn(selfupdate.t("selfupdate.untrusted"), self.ui.text())

    def test_network_error_on_signature_is_check_failed(self):
        import urllib.error
        real = selfupdate._get

        def get(url):
            if url.endswith(".sig"):
                raise urllib.error.URLError("timed out")
            return real(url)
        with mock.patch.object(selfupdate, "_get", side_effect=get), mock.patch("os.execv") as execv:
            self.assertIsNone(selfupdate.run(self.paths, self.ui, []))
        execv.assert_not_called()
        self.assertIn(selfupdate.t("selfupdate.check_failed"), self.ui.text())
        self.assertNotIn(selfupdate.t("selfupdate.untrusted"), self.ui.text())

    def test_up_to_date(self):
        with mock.patch.object(selfupdate, "__version__", "0.2.0"), mock.patch("os.execv") as execv:
            self.assertIsNone(selfupdate.run(self.paths, self.ui, []))
        execv.assert_not_called()

    def test_watch_notice_once_per_version(self):
        saved = {}
        first = selfupdate.notice(saved, now=1000.0)
        self.assertEqual(first.key, "watch.selfupdate_available")
        self.assertEqual(first.params, {"version": "0.2.0"})
        self.assertIsNone(selfupdate.notice(saved, now=1000.0 + 2 * 86400))  # same version: silent

    def test_watch_notice_checks_daily(self):
        saved = {"selfupdate": {"checked": 1000.0}}
        with mock.patch.object(selfupdate, "latest") as latest:
            self.assertIsNone(selfupdate.notice(saved, now=1000.0 + 3600))
        latest.assert_not_called()


CHANGELOG = """# Changelog

## Unreleased

- Not released yet.

## 0.3.0 – 2026-11-20

- Faster `setup`. It now **skips** steps that are done.
- A very long entry that goes on and on and on and on and on and on and on and on and on and on and on and on
  and continues on the next line.

## 0.2.0 – 2026-11-19

- Fix one thing.\x1b[31m

## 0.1.0 – 2026-11-18

- Old entry.
"""


class NewsTest(unittest.TestCase):
    def test_entries_after_the_installed_version_up_to_the_new_one(self):
        news = selfupdate.changelog_news(CHANGELOG, "0.1.0", "v0.3.0")
        self.assertEqual(news[0], "Faster setup")  # first sentence, no markdown
        self.assertTrue(news[1].endswith("…") and len(news[1]) <= selfupdate.NEWS_CHARS)
        self.assertEqual(news[2], "Fix one thing.[31m")  # the escape character is gone
        self.assertNotIn("Old entry", " ".join(news))
        self.assertNotIn("Not released", " ".join(news))
        self.assertEqual(selfupdate.changelog_news(CHANGELOG, "0.2.0", "v0.2.0"), [])

    def test_notice_lines_only_for_a_newer_version(self):
        saved = {"selfupdate": {"notified": "v0.3.0", "news": ["Faster setup"]}}
        with mock.patch.object(selfupdate, "__version__", "0.2.0"):
            lines = selfupdate.notice_lines(saved)
            self.assertIn("0.2.0", lines[0])
            self.assertIn("0.3.0", lines[0])
            self.assertIn("sudo roadie update", lines[1])
            self.assertIn("- Faster setup", lines)
            self.assertIn(selfupdate.CHANGELOG_URL, lines[-1])
        with mock.patch.object(selfupdate, "__version__", "0.3.0"):  # updated meanwhile
            self.assertEqual(selfupdate.notice_lines(saved), [])

    def test_push_names_the_news(self):
        from roadie.ops import watch
        saved = {}
        with mock.patch.object(selfupdate, "latest", return_value="v0.3.0"), \
                mock.patch.object(selfupdate, "fetch_news", return_value=["Faster setup"]), \
                mock.patch.object(selfupdate, "__version__", "0.2.0"):
            msg = selfupdate.notice(saved, now=1.0)
        self.assertEqual(saved["selfupdate"]["news"], ["Faster setup"])
        self.assertIn("- Faster setup", watch.format_messages([msg])[0][1])
        old = selfupdate.Message("info", "watch.selfupdate_available", {"version": "0.2.0"})
        self.assertIn("0.2.0", watch.format_messages([old])[0][1])  # saved by an older roadie: still works


class NoticeDisabledTest(unittest.TestCase):
    def test_no_notice_without_repo(self):
        with mock.patch.object(selfupdate, "REPO", ""), mock.patch.object(selfupdate, "latest") as latest:
            self.assertIsNone(selfupdate.notice({}, now=1.0))
        latest.assert_not_called()


class WatchTest(unittest.TestCase):
    def test_watcher_pushes_new_version(self):
        from roadie.ops import watch
        msg = selfupdate.Message("info", "watch.selfupdate_available", {"version": "0.2.0"})
        with mock.patch.object(selfupdate, "notice", return_value=msg) as notice:
            events = watch.collect_events(mock.Mock(), {}, 5.0)
        notice.assert_called_once()
        self.assertIn(msg, events)
        title, text, _ = watch.format_messages([msg])[0]
        self.assertIn("0.2.0", text)

    def test_watcher_survives_failing_check(self):
        from roadie.ops import watch
        saved = {}
        with mock.patch.object(selfupdate, "notice", side_effect=RuntimeError("boom")), \
                mock.patch("sys.stderr"):
            events = watch.collect_events(mock.Mock(), saved, 5.0)
        self.assertEqual(events, [])
        self.assertEqual(saved["last_run"], 5.0)


if __name__ == "__main__":
    unittest.main()
