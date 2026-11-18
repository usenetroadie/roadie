import json
import re
import unittest
from pathlib import Path

from roadie.answers import Answers
from roadie.render import build_render_config, load_versions
from roadie.render import system as S

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())


def cfg(reboot="05:00"):
    data = json.loads(json.dumps(EXAMPLE))
    data["maintenance"]["reboot_time"] = reboot
    return build_render_config(Answers.from_dict(data), load_versions(), mem_mb=4000, disk_gb=60)


def regexes(filter_text):
    """fail2ban failregex lines → Python regexes (<HOST> → named group, prefix dropped)."""
    block = filter_text.split("failregex =", 1)[1].split("\nignoreregex", 1)[0]
    out = []
    for line in block.strip().splitlines():
        line = line.strip().replace("%(__prefix_line)s", "").replace("<HOST>", r"(?P<host>[\d.:a-fA-F]+)")
        out.append(re.compile(line))
    return out


def hit(rxs, line):
    for rx in rxs:
        m = rx.search(line)
        if m:
            return m.group("host")
    return None


class SshTest(unittest.TestCase):
    def test_sshd_dropin_sorts_before_cloud_image_file(self):
        f = S.sshd_dropin(cfg())
        self.assertLess(Path(f.path).name, "50-cloudimg-settings.conf")
        self.assertTrue(f.path.startswith("/etc/ssh/sshd_config.d/"))

    def test_sshd_settings(self):
        text = S.sshd_dropin(cfg()).content
        for line in ("PasswordAuthentication no", "KbdInteractiveAuthentication no", "PermitRootLogin no",
                     "AllowUsers streamadmin", "AllowTcpForwarding no"):
            self.assertIn(line + "\n", text)
        self.assertNotIn("Subsystem", text)  # only where sshd allows it (Debian 12 refuses)
        self.assertIn("Subsystem sftp /usr/lib/openssh/sftp-server -l INFO\n", S.sshd_dropin(cfg(), sftp_log=True).content)


class Fail2banTest(unittest.TestCase):
    def setUp(self):
        self.files = {f.path: f.content for f in S.fail2ban_files(cfg())}

    def test_jails_present(self):
        jail = self.files["/etc/fail2ban/jail.d/roadie.conf"]
        for name in ("[sshd]", "[sshd-invalid]", "[traefik-scanner]", "[recidive]"):
            self.assertIn(name, jail)
        self.assertIn("chain=DOCKER-USER", jail)
        self.assertIn("/opt/roadie/stack/data/traefik/access.log", jail)

    def jail_section(self, jail, name):
        return jail.split(f"[{name}]", 1)[1].split("\n[", 1)[0]

    def test_own_addresses_never_banned(self):
        jail = self.files["/etc/fail2ban/jail.d/roadie.conf"]
        self.assertIn("ignoreip = 127.0.0.1/8 ::1\n", jail)
        files = {f.path: f.content for f in S.fail2ban_files(cfg(), ignore_ips=["203.0.113.10", "2001:db8::1"])}
        jail = files["/etc/fail2ban/jail.d/roadie.conf"]
        default = jail.split("[DEFAULT]", 1)[1].split("\n[", 1)[0]  # applies to every jail
        self.assertIn("ignoreip = 127.0.0.1/8 ::1 203.0.113.10 2001:db8::1\n", default)

    def test_wrong_user_name_is_not_banned_at_once(self):
        section = self.jail_section(self.files["/etc/fail2ban/jail.d/roadie.conf"], "sshd-invalid")
        retry = int(section.split("maxretry = ", 1)[1].split()[0])
        self.assertGreaterEqual(retry, 3)
        self.assertIn("findtime = 10m", section)

    def test_ssh_ports_in_jails(self):
        files = {f.path: f.content for f in S.fail2ban_files(cfg(), ssh_ports=[22, 2222])}
        jail = files["/etc/fail2ban/jail.d/roadie.conf"]
        for name in ("sshd", "sshd-invalid"):
            self.assertIn("port = 22,2222\n", self.jail_section(jail, name))

    def test_sshd_invalid_filter(self):
        rx = regexes(self.files["/etc/fail2ban/filter.d/roadie-sshd-invalid.conf"])
        self.assertEqual(hit(rx, "Invalid user admin from 203.0.113.10 port 51234"), "203.0.113.10")
        self.assertEqual(hit(rx, "User root from 203.0.113.11 not allowed because not listed in AllowUsers"), "203.0.113.11")
        self.assertEqual(hit(rx, "Connection closed by invalid user oracle 203.0.113.12 port 4 [preauth]"), "203.0.113.12")
        self.assertIsNone(hit(rx, "Accepted publickey for streamadmin from 203.0.113.10 port 5 ssh2"))
        self.assertIsNone(hit(rx, "Failed publickey for streamadmin from 203.0.113.10 port 5 ssh2"))

    def test_traefik_scanner_filter(self):
        rx = regexes(self.files["/etc/fail2ban/filter.d/roadie-traefik-scanner.conf"])
        bad = '198.51.100.7 - - [{d}] "GET {p} HTTP/1.1" 404 19 "-" "curl" 1 "-" "-" 0ms'
        for date in ("14/Nov/2026:01:02:03 +0000", ""):  # fail2ban strips the date, keeps brackets
            for path in ("/.env", "/.git/config", "/wp-login.php", "/admin/x.php?a=1", "/phpmyadmin/"):
                self.assertEqual(hit(rx, bad.format(d=date, p=path)), "198.51.100.7", (date, path))
        ok = '198.51.100.7 - - [] "GET {p} HTTP/1.1" {s} 19 "-" "Stremio" 1 "-" "-" 0ms'
        self.assertIsNone(hit(rx, ok.format(p="/stremio/u/p/stream/movie/tt1.json", s=404)))
        self.assertIsNone(hit(rx, ok.format(p="/.env", s=200)))


class UpdatesTest(unittest.TestCase):
    def test_reboot_time_used(self):
        text = S.unattended_upgrades(cfg("03:30")).content
        self.assertIn('Unattended-Upgrade::Automatic-Reboot "true";', text)
        self.assertIn('Unattended-Upgrade::Automatic-Reboot-Time "03:30";', text)
        self.assertIn('APT::Periodic::Unattended-Upgrade "1";', text)


class UfwTest(unittest.TestCase):
    def test_ssh_always_allowed_first(self):
        cmds = S.ufw_commands()
        allow_idx = cmds.index(["ufw", "limit", "22/tcp"])
        enable_idx = cmds.index(["ufw", "--force", "enable"])
        self.assertLess(allow_idx, enable_idx)
        self.assertIn(["ufw", "allow", "443/tcp"], cmds)

    def test_custom_ssh_port(self):
        cmds = S.ufw_commands(ssh_ports=[22, 2222])
        for port in ("22", "2222"):
            self.assertLess(cmds.index(["ufw", "limit", f"{port}/tcp"]), cmds.index(["ufw", "--force", "enable"]))

    def test_no_ssh_port_no_firewall(self):
        with self.assertRaises(ValueError):
            S.ufw_commands(ssh_ports=[])


class SftpProbeTest(unittest.TestCase):
    def test_probe_tries_a_throwaway_config(self):
        from unittest import mock
        from roadie import system
        from roadie.phases import host
        seen = []

        def run(cmd, **kw):
            seen.append(Path(cmd[-1]).read_text())
            return ""
        with mock.patch.object(system, "run", run):
            self.assertTrue(host.sftp_override_works())
        self.assertIn(S.SFTP_LOG_LINE, seen[0])
        with mock.patch.object(system, "run", mock.Mock(side_effect=system.CommandFailed(["sshd"], 255, "already defined"))):
            self.assertFalse(host.sftp_override_works())
