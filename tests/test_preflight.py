import dataclasses
import unittest

from roadie import preflight as P

FRESH = P.Facts(
    os_id="ubuntu", os_version="24.04", arch="x86_64", is_root=True, mem_mb=3900,
    disk_free_gb=40.0, cpu_count=2, ports_in_use=set(), docker_present=False,
    clock_synced=True, reachable={"github.com": True, "ghcr.io": True},
)

SS = """LISTEN 0      4096   127.0.0.53%lo:53  0.0.0.0:*
LISTEN 0      4096         0.0.0.0:22  0.0.0.0:*
LISTEN 0      511          0.0.0.0:80  0.0.0.0:*
LISTEN 0      511             [::]:443    [::]:*
LISTEN 0      128        127.0.0.1:8080 0.0.0.0:*
"""

MEMINFO = """MemTotal:        4015772 kB
MemFree:          123456 kB
"""


def facts(**kw):
    return dataclasses.replace(FRESH, **kw)


def keys(findings, level):
    return {f.key for f in findings if f.level == level}


class PreflightTest(unittest.TestCase):
    def test_fresh_server_has_no_errors(self):
        findings = P.evaluate(FRESH)
        self.assertFalse(P.has_errors(findings), findings)

    def test_supported_systems(self):
        for os_id, version in (("ubuntu", "24.04"), ("debian", "12"), ("debian", "13")):
            self.assertNotIn("preflight.os", keys(P.evaluate(facts(os_id=os_id, os_version=version)), "error"))
        for os_id, version in (("ubuntu", "22.04"), ("debian", "11"), ("fedora", "41"), ("debian", "24.04")):
            self.assertIn("preflight.os", keys(P.evaluate(facts(os_id=os_id, os_version=version)), "error"))

    def test_arm_and_x86_supported_others_not(self):
        self.assertNotIn("preflight.arch", keys(P.evaluate(facts(arch="aarch64")), "error"))
        self.assertNotIn("preflight.arch", keys(P.evaluate(facts(arch="x86_64")), "error"))
        self.assertIn("preflight.arch", keys(P.evaluate(facts(arch="armv7l")), "error"))

    def test_not_root_is_error(self):
        self.assertIn("preflight.root", keys(P.evaluate(facts(is_root=False)), "error"))

    def test_port_in_use_is_error(self):
        f = P.evaluate(facts(ports_in_use={443}))
        self.assertIn("preflight.ports", keys(f, "error"))

    def test_existing_docker_is_error(self):
        self.assertIn("preflight.docker", keys(P.evaluate(facts(docker_present=True)), "error"))

    def test_low_ram_warns(self):
        self.assertIn("preflight.ram_low", keys(P.evaluate(facts(mem_mb=1950)), "warn"))
        self.assertIn("preflight.ram", keys(P.evaluate(facts(mem_mb=1000)), "error"))

    def test_small_disk_is_error(self):
        self.assertIn("preflight.disk", keys(P.evaluate(facts(disk_free_gb=12)), "error"))

    def test_clock_unsynced_warns(self):
        self.assertIn("preflight.clock", keys(P.evaluate(facts(clock_synced=False)), "warn"))

    def test_unreachable_host_is_error(self):
        f = P.evaluate(facts(reachable={"github.com": True, "ghcr.io": False}))
        errs = [x for x in f if x.level == "error"]
        self.assertEqual(errs[0].key, "preflight.unreachable")
        self.assertEqual(errs[0].params["hosts"], "ghcr.io")

    def test_parse_ss_output(self):
        self.assertEqual(P.parse_listen_ports(SS) & {22, 80, 443}, {22, 80, 443})
        self.assertNotIn(53, P.parse_listen_ports(SS) - {53} )

    def test_parse_meminfo(self):
        self.assertEqual(P.parse_meminfo_mb(MEMINFO), 3921)

    def test_parse_os_release(self):
        text = 'NAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\n'
        self.assertEqual(P.parse_os_release(text), ("ubuntu", "24.04"))


class OwnInstallTest(unittest.TestCase):
    def test_rerun_after_own_install_not_blocked(self):
        f = facts(docker_present=True, ports_in_use={80, 443})
        self.assertTrue(P.has_errors(P.evaluate(f)))
        self.assertFalse(P.has_errors(P.evaluate(f, own_install=True)))

    def test_min_disk_can_be_lowered_for_tests(self):
        f = facts(disk_free_gb=12)
        self.assertFalse(P.has_errors(P.evaluate(f, min_disk_gb=8)))
