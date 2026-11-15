"""The proof step (test search at the end of the setup): never raises, ok vs. warn."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import i18n
from roadie.engine import Context
from roadie.paths import Paths
from roadie.phases import stack
from roadie.redact import Redactor
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter


class FakeClient:
    count = 0
    catalogs = 9
    error = None

    def __init__(self, base, timeout=None):
        self.base = base

    def stream_count(self, uuid, encrypted, kind, imdb_id):
        if FakeClient.error:
            raise FakeClient.error
        return FakeClient.count

    def catalog_count(self, uuid, encrypted):
        if FakeClient.error:
            raise FakeClient.error
        return FakeClient.catalogs


class ProofTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        FakeClient.count, FakeClient.catalogs, FakeClient.error = 0, 9, None
        for patch in (mock.patch.object(stack, "AioClient", FakeClient),
                      mock.patch.object(stack, "_container_url", return_value="http://10.0.0.2:3000")):
            patch.start()
            self.addCleanup(patch.stop)

    def run_proof(self, tmdb="x" * 32, answers_secret=None):
        paths = Paths(Path(self.tmp.name))
        state = State(paths.state_file)
        state.answers = {"tmdb_api_key": tmdb}
        out = io.StringIO()
        ctx = Context(paths=paths, state=state, secrets=SecretStore(paths.secrets_file),
                      ui=Prompter(inp=io.StringIO(""), out=out), redact=Redactor([]))
        if answers_secret:
            ctx.answers = mock.Mock(data={"tmdb_api_key": answers_secret})
        self.outcome = stack.proof(ctx)
        self.open_points = ctx.extra.get("open_points", [])
        return " ".join(out.getvalue().split())

    def test_proof_note_ok_and_none(self):
        FakeClient.count = 51
        self.assertEqual(self.run_proof(), "")  # the result goes into the step line
        self.assertEqual((self.outcome.status, self.outcome.note), ("ok", i18n.t("proof.note_ok", count=51)))
        self.assertEqual(self.outcome.note, "51 streams found")
        FakeClient.count = 0
        self.run_proof()
        self.assertEqual((self.outcome.status, self.outcome.note), ("warn", i18n.t("proof.note_none")))
        self.assertEqual(self.outcome.open_key, "open.proof")

    def test_no_tmdb_is_open_point(self):
        FakeClient.count = 51
        self.run_proof(tmdb=None)
        self.assertEqual(self.outcome.status, "ok")
        self.assertEqual(self.open_points, [(i18n.t("step.proof"), "open.tmdb")])
        self.assertIn("Change settings", i18n.t("open.tmdb"))
        self.run_proof(tmdb="x" * 32)
        self.assertEqual(self.open_points, [])
        self.run_proof(tmdb=None, answers_secret="x" * 32)  # real runs: secret only in ctx.answers
        self.assertEqual(self.open_points, [])

    def test_proof_warns_without_catalogs(self):
        FakeClient.count, FakeClient.catalogs = 12, 0
        self.run_proof()
        self.assertEqual(self.outcome.status, "warn")
        self.assertEqual(self.outcome.open_key, "open.catalogs")
        self.assertEqual(self.outcome.note, i18n.t("proof.note_no_catalogs", count=12))

    def test_no_streams_warns_with_causes(self):
        self.run_proof()
        self.assertEqual(self.outcome.status, "warn")
        out = " ".join(self.outcome.hints)
        self.assertIn(i18n.t("proof.causes"), out)
        self.assertIn("'Check status'", out)
        self.assertNotIn("Diagnose", out)

    def test_never_raises(self):
        for error in (RuntimeError("boom"), OSError("down"), KeyError("x")):
            FakeClient.error = error
            self.run_proof()
            self.assertEqual(self.outcome.note, i18n.t("proof.note_none"))
        with mock.patch.object(stack, "_container_url", side_effect=RuntimeError("no container")):
            self.run_proof()
            self.assertEqual(self.outcome.note, i18n.t("proof.note_none"))

    def test_german_causes_point_to_check_status(self):
        i18n.set_language("de")
        try:
            self.assertIn("„Zustand prüfen“", i18n.t("proof.causes"))
        finally:
            i18n.set_language("en")


if __name__ == "__main__":
    unittest.main()
