import unittest

from roadie import notify
from roadie.ops.watchlogic import Problem, decide, new_login_ips, parse_accepted_logins
from tests.helpers import MockHttp, closed_port

P = Problem("container:aiostreams", "watch.container", {"name": "aiostreams"})


class DecideTest(unittest.TestCase):
    def test_first_sighting_is_silent_second_reports(self):
        msgs, state = decide({}, [P], 1000)
        self.assertEqual(msgs, [])
        msgs, state = decide(state, [P], 1300)
        self.assertEqual([(m.kind, m.key) for m in msgs], [("problem", "watch.container")])

    def test_no_repeat_before_12h_then_reminder(self):
        _, state = decide({}, [P], 0)
        _, state = decide(state, [P], 300)
        msgs, state = decide(state, [P], 600)
        self.assertEqual(msgs, [])
        msgs, state = decide(state, [P], 300 + 12 * 3600)
        self.assertEqual([m.kind for m in msgs], ["reminder"])

    def test_resolved_only_if_it_was_reported(self):
        _, state = decide({}, [P], 0)
        msgs, state = decide(state, [], 300)
        self.assertEqual(msgs, [])
        _, state = decide({}, [P], 0)
        _, state = decide(state, [P], 300)
        msgs, state = decide(state, [], 600)
        self.assertEqual([(m.kind, m.params) for m in msgs], [("resolved", {"name": "aiostreams"})])
        self.assertEqual(state, {})


class LoginTest(unittest.TestCase):
    def test_parse_and_new_ip(self):
        text = ("Accepted publickey for alice from 203.0.113.10 port 51234 ssh2: ED25519 SHA256:x\n"
                "Failed password for root from 198.51.100.1 port 1 ssh2\n"
                "Accepted publickey for alice from 203.0.113.11 port 2 ssh2\n")
        logins = parse_accepted_logins(text)
        self.assertEqual(logins, [("alice", "203.0.113.10"), ("alice", "203.0.113.11")])
        flagged, known = new_login_ips(["203.0.113.10"], logins)
        self.assertEqual(flagged, [("alice", "203.0.113.10", False), ("alice", "203.0.113.11", True)])
        self.assertEqual(known, ["203.0.113.10", "203.0.113.11"])


class NotifyTest(unittest.TestCase):
    def test_push_sends_title_priority_body(self):
        got = {}

        def route(body, headers):
            got.update(body=body, title=headers.get("Title"), prio=headers.get("Priority"))
            return 200, "{}"
        srv = MockHttp({"POST /my-topic": route})
        try:
            self.assertTrue(notify.push("my-topic", "Server", "Größe ok", priority=4, server=srv.url))
        finally:
            srv.close()
        self.assertEqual((got["body"].decode(), got["title"], got["prio"]), ("Größe ok", "Server", "4"))

    def test_push_failure_returns_false(self):
        self.assertFalse(notify.push("t", "a", "b", server=f"http://127.0.0.1:{closed_port()}"))

    def test_ipv4_only_is_restored(self):
        import socket
        before = socket.getaddrinfo
        with notify._ipv4_only():
            self.assertIsNot(socket.getaddrinfo, before)
        self.assertIs(socket.getaddrinfo, before)


class WatchCollectTest(unittest.TestCase):
    def test_container_problems(self):
        from roadie.ops.watch import container_problems
        services = [{"Service": "traefik", "State": "running", "Health": "healthy"},
                    {"Service": "aiostreams", "State": "running", "Health": "unhealthy"},
                    {"Service": "authelia", "State": "exited", "Health": ""}]
        got = {(p.id, p.key) for p in container_problems(services, ["traefik", "authelia", "aiostreams", "prowlarr"])}
        self.assertEqual(got, {("container:aiostreams", "watch.container_unhealthy"),
                               ("container:authelia", "watch.container_down"),
                               ("container:prowlarr", "watch.container_down")})

    def test_every_watch_key_is_translated(self):
        import re
        from pathlib import Path
        from roadie import i18n
        src = Path(__file__).parent.parent / "roadie/ops"
        keys = set()
        for f in src.glob("*.py"):
            keys |= set(re.findall(r'"((?:watch|backup|status|diagnose|update|ops)\.[a-z_.]+)"', f.read_text()))
        keys = {k for k in keys if not k.endswith(".json")}  # file names, not text keys
        missing = sorted(k for k in keys if k not in i18n.load("en"))
        self.assertEqual(missing, [])


class BackupLogicTest(unittest.TestCase):
    def test_file_name_and_second_backup_same_day(self):
        import datetime
        from roadie.ops.backup import file_name
        now = datetime.datetime(2026, 11, 15, 4, 7)
        self.assertEqual(file_name(now, set()), "roadie-backup-2026-11-15.tar.age")
        self.assertEqual(file_name(now, {"roadie-backup-2026-11-15.tar.age"}),
                         "roadie-backup-2026-11-15-0407.tar.age")

    def test_prune_keeps_two_newest(self):
        from roadie.ops.backup import prune
        names = [f"roadie-backup-2026-11-{d:02d}.tar.age" for d in (1, 8, 15)] + ["notes.txt", "x.tar.age.sha256"]
        self.assertEqual(prune(names), ["roadie-backup-2026-11-01.tar.age"])
        self.assertEqual(prune(names[:2]), [])

    def test_prune_second_backup_of_a_day_is_the_newer_one(self):
        from roadie.ops.backup import prune
        names = ["roadie-backup-2026-11-15.tar.age", "roadie-backup-2026-11-15-1030.tar.age",
                 "roadie-backup-2026-11-22.tar.age"]
        self.assertEqual(prune(names), ["roadie-backup-2026-11-15.tar.age"])

    def test_encrypt_and_restore_roundtrip(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from roadie.ops import backup
        from roadie.paths import Paths
        if not shutil.which("age"):
            self.skipTest("age not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src, dst = tmp / "src", tmp / "dst"
            (src / "etc/roadie").mkdir(parents=True)
            (src / "opt/roadie/stack/data/aiostreams/cache").mkdir(parents=True)
            (src / "etc/roadie/state.json").write_text('{"done": ["packages"], "answers": {"language": "en", "_ssh_hardened": true}}')
            (src / "opt/roadie/stack/compose.yaml").write_text("x")
            (src / "opt/roadie/stack/data/aiostreams/cache/big.bin").write_text("cache")
            (src / "opt/roadie/app/roadie").mkdir(parents=True)
            (src / "opt/roadie/app/roadie/cli.py").write_text("old program")
            (src / "opt/roadie/app.prev/roadie").mkdir(parents=True)  # left by a self-update
            dst.mkdir()
            key_file = tmp / "key.txt"
            subprocess.run(["age-keygen", "-o", str(key_file)], check=True, capture_output=True)
            public = [l.split(": ")[1] for l in key_file.read_text().splitlines() if l.startswith("# public key")][0]
            archive = tmp / "b.tar.age"
            backup.encrypt_tar(archive, public, src)
            self.assertNotIn(b"compose.yaml", archive.read_bytes())
            self.assertEqual(backup.restore(Paths(dst), str(archive), str(key_file)), 0)
            self.assertTrue((dst / "opt/roadie/stack/compose.yaml").exists())
            self.assertFalse((dst / "opt/roadie/stack/data/aiostreams/cache/big.bin").exists())
            self.assertFalse((dst / "opt/roadie/app").exists())  # the program is never restored over a newer one
            self.assertFalse((dst / "opt/roadie/app.prev").exists())
            import json
            state = json.loads((dst / "etc/roadie/state.json").read_text())
            self.assertEqual(state["done"], [])
            self.assertNotIn("_ssh_hardened", state["answers"])
            self.assertEqual(backup.restore(Paths(dst), str(archive), str(archive), overwrite=True), 1)  # wrong key

    def test_backup_does_not_follow_planted_links(self):
        import os
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from roadie.ops import backup
        if not shutil.which("age"):
            self.skipTest("age not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "src/etc/roadie").mkdir(parents=True)
            (tmp / "src/etc/roadie/state.json").write_text("{}")
            victim = tmp / "victim"
            victim.write_text("keep me")
            out = tmp / "backups"
            out.mkdir()
            target = out / "roadie-backup-2026-11-15.tar.age"
            os.symlink(victim, out / (target.name + ".part"))
            subprocess.run(["age-keygen", "-o", str(tmp / "key.txt")], check=True, capture_output=True)
            public = [l.split(": ")[1] for l in (tmp / "key.txt").read_text().splitlines() if l.startswith("# public key")][0]
            backup.encrypt_tar(target, public, tmp / "src", includes=["etc/roadie"])
            self.assertEqual(victim.read_text(), "keep me")
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            os.symlink(victim, out / (target.name + ".sha256"))
            backup.write_checksum(target)
            self.assertEqual(victim.read_text(), "keep me")
            self.assertIn(target.name, (out / (target.name + ".sha256")).read_text())

    def test_backup_contains_person_secrets(self):
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from roadie.ops import backup
        from roadie.paths import Paths
        if not shutil.which("age"):
            self.skipTest("age not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src, dst = tmp / "src", tmp / "dst"
            (src / "etc/roadie").mkdir(parents=True)
            (src / "opt/roadie/stack").mkdir(parents=True)
            dst.mkdir()
            answers = {"people": [{"id": "main", "name": "Main", "mode": "main"},
                                  {"id": "anna", "name": "Anna", "mode": "inherit"}]}
            (src / "etc/roadie/state.json").write_text(json.dumps({"done": [], "answers": answers}))
            secrets = {"aiostreams_config_uuid": "u-main", "person.anna.uuid": "u-anna",
                       "person.anna.password": "pw-anna", "person.anna.encrypted": "enc-anna"}
            (src / "etc/roadie/secrets.json").write_text(json.dumps(secrets))
            key_file = tmp / "key.txt"
            subprocess.run(["age-keygen", "-o", str(key_file)], check=True, capture_output=True)
            public = [l.split(": ")[1] for l in key_file.read_text().splitlines() if l.startswith("# public key")][0]
            archive = tmp / "b.tar.age"
            backup.encrypt_tar(archive, public, src)
            self.assertEqual(backup.restore(Paths(dst), str(archive), str(key_file)), 0)
            self.assertEqual(json.loads((dst / "etc/roadie/secrets.json").read_text()), secrets)
            state = json.loads((dst / "etc/roadie/state.json").read_text())
            self.assertEqual([p["id"] for p in state["answers"]["people"]], ["main", "anna"])

    def test_restore_refuses_files_outside_the_wizard_folders(self):
        import io
        from unittest import mock
        import shutil
        import subprocess
        import tarfile
        import tempfile
        from pathlib import Path
        from roadie.ops import backup
        from roadie.paths import Paths
        if not shutil.which("age"):
            self.skipTest("age not installed")

        def add(tar, name, data=b"x", kind=tarfile.REGTYPE, link="", uid=0):
            info = tarfile.TarInfo(name)
            info.type, info.linkname, info.size = kind, link, len(data) if kind == tarfile.REGTYPE else 0
            info.uid = uid
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            key_file = tmp / "key.txt"
            subprocess.run(["age-keygen", "-o", str(key_file)], check=True, capture_output=True)
            public = [l.split(": ")[1] for l in key_file.read_text().splitlines() if l.startswith("# public key")][0]
            cases = {
                "outside": [("etc/roadie/state.json", {}), ("etc/sudoers.d/x", {})],
                "dotdot": [("etc/roadie/../cron.d/x", {})],
                "absolute": [("/etc/roadie/state.json", {})],
                "symlink": [("opt/roadie/l", {"kind": tarfile.SYMTYPE, "link": "/etc"}),
                            ("opt/roadie/l/sudoers.d/x", {})],
                "prefix-trick": [("opt/roadie-evil/x", {})],
                "dot-slash": [("./etc/roadie/state.json", {})],
                "double-slash": [("etc//roadie/state.json", {})],
                "inner-dot": [("etc/roadie/./state.json", {})],
                "foreign-owner": [("etc/roadie/state.json", {"uid": 1000})],
                "owner-outside-prowlarr": [("opt/roadie/stack/data/aiostreams/db.sqlite", {"uid": 1000})],
            }
            for case, members in cases.items():
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                    for name, kw in members:
                        add(tar, name, **kw)
                archive = tmp / f"{case}.tar.age"
                subprocess.run(["age", "-r", public, "-o", str(archive)], input=buf.getvalue(), check=True)
                dst = tmp / f"dst-{case}"
                dst.mkdir()
                with mock.patch.object(backup.os, "geteuid", return_value=0):  # as on the server
                    self.assertEqual(backup.restore(Paths(dst), str(archive), str(key_file)), 1, case)
                self.assertEqual(list(dst.iterdir()), [], case)  # nothing at all was written

    def _restore_archive(self, tmp, members):
        """members: (name, uid) of regular files; returns (archive, key file)."""
        import io
        import subprocess
        import tarfile
        key_file = tmp / "key.txt"
        if not key_file.exists():
            subprocess.run(["age-keygen", "-o", str(key_file)], check=True, capture_output=True)
        public = [l.split(": ")[1] for l in key_file.read_text().splitlines() if l.startswith("# public key")][0]
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, uid in members:
                info = tarfile.TarInfo(name)
                data = b'{"answers": {}}'
                info.size, info.uid = len(data), uid
                tar.addfile(info, io.BytesIO(data))
        archive = tmp / f"a{len(list(tmp.iterdir()))}.tar.age"
        subprocess.run(["age", "-r", public, "-o", str(archive)], input=buf.getvalue(), check=True)
        return archive, key_file

    def test_restore_keeps_prowlarr_owner(self):
        import os
        import shutil
        import tempfile
        from pathlib import Path
        from roadie.ops import backup
        from roadie.paths import Paths
        if not shutil.which("age"):
            self.skipTest("age not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            archive, key = self._restore_archive(tmp, [("etc/roadie/state.json", 0),
                                                       ("opt/roadie/stack/data/prowlarr/prowlarr.db", 1000)])
            dst = tmp / "dst"
            dst.mkdir()
            self.assertEqual(backup.restore(Paths(dst), str(archive), str(key)), 0)
            if os.geteuid() == 0:
                self.assertEqual((dst / "opt/roadie/stack/data/prowlarr/prowlarr.db").stat().st_uid, 1000)

    def test_restore_refuses_existing_installation_without_flag(self):
        import shutil
        import tempfile
        from pathlib import Path
        from roadie.ops import backup
        from roadie.paths import Paths
        if not shutil.which("age"):
            self.skipTest("age not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            archive, key = self._restore_archive(tmp, [("etc/roadie/state.json", 0)])
            dst = tmp / "dst"
            (dst / "etc/roadie").mkdir(parents=True)
            (dst / "etc/roadie/state.json").write_text("{}")  # a running installation
            self.assertEqual(backup.restore(Paths(dst), str(archive), str(key)), 2)
            self.assertEqual((dst / "etc/roadie/state.json").read_text(), "{}")
            self.assertEqual(backup.restore(Paths(dst), str(archive), str(key), overwrite=True), 0)
            self.assertIn('"done"', (dst / "etc/roadie/state.json").read_text())

    def test_restore_refuses_existing_symlinks_on_the_way(self):
        import os
        import shutil
        import tempfile
        from pathlib import Path
        from roadie.ops import backup
        from roadie.paths import Paths
        if not shutil.which("age"):
            self.skipTest("age not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            archive, key = self._restore_archive(tmp, [("opt/roadie/stack/compose.yaml", 0)])
            for link in ("opt/roadie", "opt/roadie/stack/compose.yaml"):
                dst = tmp / f"dst-{link.count('/')}"
                elsewhere = dst / "srv/elsewhere"  # inside the restore root, like /srv on a real server
                (elsewhere / "stack").mkdir(parents=True)
                (elsewhere / "file").write_text("keep")
                target = elsewhere if link == "opt/roadie" else elsewhere / "file"
                (dst / link).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, dst / link)
                self.assertEqual(backup.restore(Paths(dst), str(archive), str(key)), 1, link)
                self.assertEqual(sorted(p.name for p in elsewhere.rglob("*")), ["file", "stack"], link)
                self.assertEqual((elsewhere / "file").read_text(), "keep", link)

    def test_restore_without_age_program_is_explained_not_crashing(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from roadie.ops import backup
        from roadie.paths import Paths
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.tar.age", "key.txt"):
                (Path(tmp) / name).write_text("x")
            with mock.patch("shutil.which", return_value=None), \
                 mock.patch("roadie.system.run", side_effect=backup.system.CommandFailed(["apt-get"], 100, "no net")):
                self.assertEqual(backup.restore(Paths(Path(tmp)), f"{tmp}/a.tar.age", f"{tmp}/key.txt"), 1)


class UpdateLogicTest(unittest.TestCase):
    def test_pending_updates(self):
        from roadie.ops.update import pending, running_images
        compose = 'services:\n  traefik:\n    image: "traefik:v3.7.17"\n    restart: "always"\n  aiostreams:\n    image: "ghcr.io/viren070/aiostreams:v2.37.0"\n'
        current = running_images(compose)
        self.assertEqual(current, {"traefik": "traefik:v3.7.17", "aiostreams": "ghcr.io/viren070/aiostreams:v2.37.0"})
        versions = {"traefik": {"image": "traefik", "tag": "v3.7.18"},
                    "aiostreams": {"image": "ghcr.io/viren070/aiostreams", "tag": "v2.37.0"}}
        self.assertEqual(pending(current, versions), [("traefik", "traefik:v3.7.17", "traefik:v3.7.18")])


class OldPythonTarTest(unittest.TestCase):
    """Debian 12 ships Python 3.11.2: tarfile has no extraction filters there."""

    def test_selfupdate_install_without_tar_filter(self):
        import io, tarfile, tempfile
        from pathlib import Path
        from unittest import mock
        from roadie.ops import selfupdate
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            archive = tmp / "r.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                for name, data in (("roadie-v9.9.9/roadie/__init__.py", b'__version__ = "9.9.9"\n'),
                                   ("roadie-v9.9.9/roadie/__main__.py", b'print("roadie 9.9.9")\n')):
                    info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o4755
                    tar.addfile(info, io.BytesIO(data))
            app = tmp / "app"; (app / "roadie").mkdir(parents=True)
            with mock.patch.object(tarfile, "data_filter", create=True), \
                    mock.patch.dict(tarfile.__dict__):
                del tarfile.__dict__["data_filter"]
                self.assertEqual(selfupdate.install(archive, app, "v9.9.9"), "ok")
            installed = app / "roadie/__main__.py"
            self.assertTrue(installed.exists())
            self.assertEqual(installed.stat().st_mode & 0o7777, 0o755)  # setuid gone, as with filter="data"
