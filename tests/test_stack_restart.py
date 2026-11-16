"""A changed config file reaches a running container: stack_up recreates exactly the
services whose rendered files changed (compose up alone ignores mounted files)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from roadie.engine import Context
from roadie.paths import Paths
from roadie.phases import stack
from roadie.redact import Redactor
from roadie.render import RenderedFile
from roadie.secretstore import SecretStore
from roadie.state import State


def files(authelia="a", routes="r", aio="x"):
    return [RenderedFile("compose.yaml", "services: {}\n"),
            RenderedFile("config/traefik/traefik.yml", "static\n"),
            RenderedFile("config/traefik/routes.yml", routes),
            RenderedFile("config/authelia/configuration.yml", authelia),
            RenderedFile("secrets/authelia/JWT_SECRET", "s", 0o600),
            RenderedFile("env/authelia.env", "A=1\n", 0o600),
            RenderedFile("env/aiostreams.env", aio, 0o600),
            RenderedFile("env/prowlarr.env", "P=1\n", 0o600)]


class StackRestartTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        paths = Paths(root=Path(tmp.name))
        (Path(tmp.name) / "etc/roadie").mkdir(parents=True)
        self.ctx = Context(paths=paths, state=State(paths.state_file), secrets=SecretStore(paths.secrets_file),
                           ui=None, redact=Redactor())
        self.running = "traefik\nauthelia\naiostreams\nprowlarr\n"
        self.calls = []
        original = stack._compose

        def fake(ctx, *args, check=True):
            self.calls.append(args)
            return self.running if args[:2] == ("ps", "--services") else ""
        stack._compose = fake
        self.addCleanup(setattr, stack, "_compose", original)

    def up(self, rendered):
        self.calls = []
        self.ctx.extra["config_hashes"] = stack.config_hashes(rendered)
        stack.stack_up(self.ctx)
        return [c for c in self.calls if "--force-recreate" in c]

    def test_changed_authelia_config_recreates_authelia_only(self):
        self.up(files())
        self.assertEqual(self.up(files(authelia="cookie-only")),
                         [("up", "-d", "--no-deps", "--force-recreate", "authelia")])

    def test_unchanged_files_no_restart(self):
        self.up(files())
        self.assertEqual(self.up(files()), [])

    def test_dynamic_traefik_routes_need_no_restart(self):
        self.up(files())
        self.assertEqual(self.up(files(routes="new route")), [])

    def test_env_change_recreates_that_service(self):
        self.up(files())
        self.assertEqual(self.up(files(aio="y")), [("up", "-d", "--no-deps", "--force-recreate", "aiostreams")])

    def test_first_start_recreates_nothing(self):
        self.running = ""
        self.assertEqual(self.up(files()), [])
        self.running = "traefik\nauthelia\naiostreams\nprowlarr\n"
        self.assertEqual(self.up(files()), [])  # hashes were recorded on the first start

    def test_existing_install_without_hashes_recreates_running_services_once(self):
        self.running = "authelia\naiostreams\n"
        self.assertEqual(self.up(files()),
                         [("up", "-d", "--no-deps", "--force-recreate", "aiostreams", "authelia")])
        self.assertEqual(self.up(files()), [])

    def test_recreate_happens_after_up(self):
        self.up(files())
        self.up(files(authelia="b"))
        names = [c[:2] for c in self.calls]
        self.assertLess(names.index(("ps", "--services")), names.index(("up", "-d")))
        self.assertIn("--force-recreate", self.calls[-1])


def _gpg_key(home: Path, name: str) -> tuple[str, str]:
    """A throwaway signing key: (fingerprint, armored public key)."""
    import subprocess
    subprocess.run(["gpg", "--homedir", str(home), "--batch", "--passphrase", "", "--quick-gen-key",
                    f"{name} <{name}@example.com>", "ed25519", "sign", "never"], check=True, capture_output=True)
    out = subprocess.run(["gpg", "--homedir", str(home), "--with-colons", "--list-keys", name],
                         check=True, capture_output=True, text=True).stdout
    fpr = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:"))
    armored = subprocess.run(["gpg", "--homedir", str(home), "--armor", "--export", fpr],
                             check=True, capture_output=True, text=True).stdout
    return fpr, armored


@unittest.skipUnless(shutil.which("gpg"), "gpg missing")
class DockerKeyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name) / "gpg"
        home.mkdir(mode=0o700)
        cls.good_fpr, cls.good = _gpg_key(home, "docker")
        cls.evil_fpr, cls.evil = _gpg_key(home, "attacker")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def keyring(self, text):
        from unittest import mock
        path = Path(self.tmp.name) / "download.asc"
        path.write_text(text)
        with mock.patch.object(stack, "DOCKER_KEY_FINGERPRINT", self.good_fpr):
            return stack.pinned_keyring(path)

    def keys_in(self, armored):
        import subprocess
        with tempfile.TemporaryDirectory() as home:
            subprocess.run(["gpg", "--homedir", home, "--batch", "--import"], input=armored, text=True,
                           check=True, capture_output=True)
            out = subprocess.run(["gpg", "--homedir", home, "--with-colons", "--list-keys"],
                                 check=True, capture_output=True, text=True).stdout
        return [line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:")]

    def test_only_the_pinned_key_is_accepted(self):
        armored = self.keyring(self.good)
        self.assertEqual(self.keys_in(armored), [self.good_fpr])

    def test_extra_key_in_the_download_is_refused(self):
        self.assertIsNone(self.keyring(self.good + self.evil))
        self.assertIsNone(self.keyring(self.evil + self.good))

    def test_wrong_key_or_garbage_is_refused(self):
        self.assertIsNone(self.keyring(self.evil))
        self.assertIsNone(self.keyring("not a key"))

    def test_docker_step_writes_nothing_without_the_right_key(self):
        from unittest import mock
        from roadie.engine import StepFailed

        def run(cmd, **kw):
            if cmd[0] == "curl":
                Path(cmd[cmd.index("-o") + 1]).write_text(self.good + self.evil)
                return ""
            if cmd[0] == "gpg":
                return system_run(cmd, **kw)
            if cmd[0] == "dpkg":
                return "amd64\n"
            calls.append(cmd)
            return ""
        from roadie import system as real_system
        system_run, calls = real_system.run, []
        with mock.patch.object(stack.system, "ok", return_value=False), \
                mock.patch.object(stack.system, "run", side_effect=run), \
                mock.patch.object(stack, "DOCKER_KEY_FINGERPRINT", self.good_fpr), \
                mock.patch.object(stack, "atomic_write") as write:
            with self.assertRaises(StepFailed):
                stack.docker(None)
        write.assert_not_called()  # neither keyring nor docker.list
        self.assertEqual(calls, [])  # no apt-get, no systemctl


if __name__ == "__main__":
    unittest.main()


class DockerSourceTest(unittest.TestCase):
    def test_ubuntu_and_debian_any_arch(self):
        from roadie.phases.stack import docker_source
        url, line = docker_source('ID=ubuntu\nVERSION_CODENAME=noble\n', "arm64")
        self.assertEqual(url, "https://download.docker.com/linux/ubuntu/gpg")
        self.assertEqual(line.format(keyring="/k"),
                         "deb [arch=arm64 signed-by=/k] https://download.docker.com/linux/ubuntu noble stable\n")
        url, line = docker_source('PRETTY_NAME="Debian GNU/Linux 13"\nID=debian\nVERSION_CODENAME=trixie\n', "amd64")
        self.assertIn("/linux/debian trixie stable", line)
        for bad in (('ID=fedora\nVERSION_CODENAME=x\n', "amd64"), ('ID=debian\n', "amd64"),
                    ('ID=ubuntu\nVERSION_CODENAME=noble\n', "amd64 evil")):
            with self.assertRaises(ValueError):
                docker_source(*bad)
