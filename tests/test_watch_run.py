"""Watcher run: failed pushes are sent again; apps stopped by a running backup are no alarm."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from roadie.ops import backup, watch
from roadie.ops.common import BACKUP_MARKER, Env
from roadie.ops.watchlogic import Message
from roadie.paths import Paths
from roadie.secretstore import SecretStore
from roadie.state import State


def make_env(tmp: str) -> Env:
    paths = Paths(Path(tmp))
    state = State(paths.state_file)
    state.answers.update({"admin_user": "nobody-roadie-test",
                          "duckdns": {"subdomain": "example"}, "backup": {"age_recipient": "age1test"}})
    secrets = SecretStore(paths.secrets_file)
    secrets.set("ntfy_topic", "topic-for-tests")
    return Env(paths, state, secrets)


class UnsentPushTest(unittest.TestCase):
    def run_watch(self, env, push_ok, events=()):
        sent = []

        def push(topic, title, text, priority, server):
            sent.append(text)
            return push_ok

        with mock.patch.object(watch, "fix_passkeys"), \
                mock.patch.object(watch, "collect_problems", return_value=[]), \
                mock.patch.object(watch, "collect_events", return_value=list(events)), \
                mock.patch("roadie.notify.push", side_effect=push):
            watch.run(env)
        return sent

    def test_failed_push_is_sent_again_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            login = Message("info", "watch.ssh_login_new", {"user": "admin", "ip": "203.0.113.10"})
            self.assertEqual(len(self.run_watch(env, push_ok=False, events=[login])), 1)
            self.assertEqual(len(env.load_json("watch.json")["unsent"]), 1)
            again = self.run_watch(env, push_ok=True)
            self.assertEqual(len(again), 1)
            self.assertIn("203.0.113.10", again[0])
            self.assertEqual(env.load_json("watch.json")["unsent"], [])
            self.assertEqual(self.run_watch(env, push_ok=True), [])  # delivered: never again

    def test_unsent_list_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            many = [Message("info", "watch.rebooted") for _ in range(watch.MAX_UNSENT + 10)]
            self.run_watch(env, push_ok=False, events=many)
            self.assertEqual(len(env.load_json("watch.json")["unsent"]), watch.MAX_UNSENT)


class BackupQuietTest(unittest.TestCase):
    SERVICES = [{"Service": s, "State": "exited"} for s in ("traefik", "authelia", "aiostreams", "prowlarr")]

    def problems(self, env, now):
        with mock.patch.object(Env, "services", return_value=self.SERVICES), \
                mock.patch.object(watch, "_outside_problems", return_value=[]), \
                mock.patch.object(watch, "_indexer_problems", return_value=[]):
            return {p.id for p in watch.collect_problems(env, now)}

    def test_apps_stopped_by_backup_are_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            now = time.time()
            self.assertIn("container:aiostreams", self.problems(env, now))
            env.save_json(BACKUP_MARKER, {"since": now - 60})
            found = self.problems(env, now)
            self.assertIn("container:traefik", found)  # Traefik is never stopped by the backup
            self.assertFalse({"container:aiostreams", "container:authelia", "container:prowlarr"} & found)
            env.save_json(BACKUP_MARKER, {"since": now - watch.BACKUP_MAX_SECONDS - 1})  # crash leftover
            self.assertIn("container:aiostreams", self.problems(env, now))

    def test_aiometadata_expected_only_while_it_is_in_the_stack(self):
        """Its id stays in the secrets after it was taken down (TMDB key removed, smaller server)."""
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            env.secrets.set("aiometadata_uuid", "META-UUID")
            now = time.time()
            self.assertNotIn("container:aiometadata", self.problems(env, now))
            env.paths.stack_dir.mkdir(parents=True)
            (env.paths.stack_dir / "compose.yaml").write_text("name: roadie\nservices:\n  aiostreams:\n    image: x\n  aiometadata:\n    image: y\n"
                                                             "  aiometadata-redis:\n    image: z\nnetworks:\n  roadie:\n    name: roadie\n")
            found = self.problems(env, now)
            self.assertTrue({"container:aiometadata", "container:aiometadata-redis"} <= found)
            self.assertNotIn("container:roadie", found)  # the network is no service

    def test_a_service_that_is_just_starting_is_no_problem(self):
        services = [{"Service": "aiostreams", "State": "running", "Health": "starting"},
                    {"Service": "prowlarr", "State": "running", "Health": "unhealthy"}]
        found = [p.id for p in watch.container_problems(services, [])]
        self.assertEqual(found, ["container:prowlarr"])

    def test_backup_sets_marker_while_running_and_removes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = make_env(tmp)
            seen = []

            def fake_encrypt(target, key, root):
                seen.append((env.var_dir / BACKUP_MARKER).exists())
                raise RuntimeError("disk full")

            running = [{"Service": "aiostreams", "State": "running"}]
            with mock.patch.object(backup, "backup_dir", return_value=Path(tmp) / "out"), \
                    mock.patch.object(Env, "services", return_value=running), \
                    mock.patch.object(Env, "compose"), mock.patch.object(Env, "push"), \
                    mock.patch.object(backup, "encrypt_tar", side_effect=fake_encrypt):
                self.assertEqual(backup.run(env, quiet=True), 1)
            self.assertEqual(seen, [True])
            self.assertFalse((env.var_dir / BACKUP_MARKER).exists())  # also after a failure


if __name__ == "__main__":
    unittest.main()


class BackupDownloadTest(unittest.TestCase):
    """One reminder per backup when it was not downloaded within 36 hours (needs SFTP logging)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = make_env(tmp.name)
        self.backups = Path(tmp.name) / "backups"
        self.backups.mkdir()
        self.file = self.backups / "roadie-backup-2026-11-15.tar.age"
        self.file.write_bytes(b"x" * 1000)
        self.made = self.file.stat().st_mtime
        dropin = Path(tmp.name) / "etc/ssh/sshd_config.d/01-roadie.conf"
        dropin.parent.mkdir(parents=True)
        dropin.write_text("PermitRootLogin no\nSubsystem sftp /usr/lib/openssh/sftp-server -l INFO\n")
        self.dropin = dropin
        self.journal = ""
        for target, value in (("roadie.ops.backup.backup_dir", lambda env: self.backups),
                              ("roadie.ops.watch.system.run", lambda *a, **k: self.journal)):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def check(self, saved, hours):
        return watch.backup_download_reminder(self.env, saved, self.made + hours * 3600)

    def test_reminds_once_when_not_downloaded(self):
        saved = {}
        self.assertIsNone(self.check(saved, 10))
        message = self.check(saved, 40)
        self.assertEqual((message.key, message.params["file"]), ("watch.backup_not_downloaded", self.file.name))
        self.assertIsNone(self.check(saved, 52))

    def test_complete_download_counts(self):
        saved = {}
        self.journal = f'close "{self.file}" bytes read 400 written 0\n'
        self.assertIsNone(self.check(saved, 1))
        self.assertIsNotNone(self.check(saved, 40))  # only part of the file was sent
        saved = {}
        self.journal = f'open "{self.file}"\nclose "{self.file}" bytes read 1000 written 0\n'
        self.assertIsNone(self.check(saved, 40))

    def test_nothing_without_sftp_logging(self):
        self.dropin.write_text("PermitRootLogin no\n")
        self.assertIsNone(self.check({}, 40))
