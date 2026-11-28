import unittest
from unittest import mock

from roadie.ops import common


class MissedProwlarrTest(unittest.TestCase):
    """AIOStreams reads Prowlarr's indexers only when it starts; if Prowlarr was not ready yet,
    the Indexers field of the Prowlarr addon stays empty until AIOStreams restarts."""

    def runner(self, started="2026-11-28T18:00:00.1Z", logs=""):
        def run(cmd, **kw):
            if cmd[:2] == ["docker", "inspect"]:
                return started + "\n"
            if cmd[:2] == ["docker", "logs"]:
                self.assertIn("--since", cmd)
                self.assertIn(started, cmd)
                return logs
            return ""
        return run

    def test_detected_from_the_log_since_its_start(self):
        with mock.patch.object(common.system, "run", side_effect=self.runner(logs="x\nFailed to initialise Prowlarr: ECONNREFUSED\n")):
            self.assertEqual(common.aiostreams_missed_prowlarr("cid"), "2026-11-28T18:00:00.1Z")

    def test_not_missed(self):
        with mock.patch.object(common.system, "run", side_effect=self.runner(logs="all fine\n")):
            self.assertIsNone(common.aiostreams_missed_prowlarr("cid"))

    def test_docker_trouble_is_no_alarm(self):
        with mock.patch.object(common.system, "run", side_effect=common.system.CommandFailed(["docker"], 1, "")):
            self.assertIsNone(common.aiostreams_missed_prowlarr("cid"))

    def test_compose_starts_aiostreams_after_prowlarr(self):
        import json
        from pathlib import Path
        from roadie.answers import Answers
        from roadie.render import build_render_config, load_versions
        from roadie.render.compose import compose_dict
        example = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())
        cfg = build_render_config(Answers.from_dict(example), load_versions(), mem_mb=4000, disk_gb=60)
        self.assertEqual(compose_dict(cfg)["services"]["aiostreams"]["depends_on"],
                         {"prowlarr": {"condition": "service_healthy"}})


class WatchRestartsTest(unittest.TestCase):
    def test_restarted_once_per_start(self):
        from roadie.ops import watch
        env = mock.Mock()
        env.compose.return_value = "cid\n"
        saved = {}
        with mock.patch.object(watch, "aiostreams_missed_prowlarr", return_value="T1"):
            self.assertTrue(watch.restart_if_prowlarr_missed(env, saved))
            self.assertFalse(watch.restart_if_prowlarr_missed(env, saved))  # same start: never in a loop
        restarts = [c for c in env.compose.call_args_list if c.args[:1] == ("restart",)]
        self.assertEqual(len(restarts), 1)
        with mock.patch.object(watch, "aiostreams_missed_prowlarr", return_value=None):
            self.assertFalse(watch.restart_if_prowlarr_missed(env, saved))
