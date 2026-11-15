import base64
import json
import unittest

from roadie.aiostreams.api import AioClient, AioError
from tests.helpers import MockHttp, closed_port


def ok(data, status=200):
    return status, json.dumps({"success": True, "data": data, "error": None}), "application/json"


def err(status, code, message):
    return status, json.dumps({"success": False, "data": None, "error": {"code": code, "message": message}}), "application/json"


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.saved = {}

        def create(body, headers):
            req = json.loads(body)
            if not req["config"].get("presets"):
                return err(400, "USER_INVALID_CONFIG", "At least one addon is required")
            self.saved = req
            return ok({"uuid": "u-1", "encryptedPassword": "ENC"}, 201)

        def update(body, headers):
            expected = "Basic " + base64.b64encode(b"u-1:pw").decode()
            if headers.get("Authorization") != expected:
                return err(401, "USER_INVALID_PASSWORD", "bad")
            self.saved["config"] = json.loads(body)["config"]
            return ok({})

        def delete(body, headers):
            expected = "Basic " + base64.b64encode(b"u-1:pw").decode()
            if headers.get("Authorization") != expected:
                return err(401, "USER_INVALID_PASSWORD", "bad")
            self.saved = {"deleted": True}
            return ok({})

        self.srv = MockHttp({
            "/api/v1/health": lambda q, h: ok(None),
            "/api/v1/templates": lambda q, h: ok([{"metadata": {"id": "builtin.debrid-starter"}, "config": {"a": 1}}]),
            "POST /api/v1/user": create,
            "PUT /api/v1/user": update,
            "DELETE /api/v1/user": delete,
            "/api/v1/user": lambda q, h: ok({"userData": self.saved.get("config"), "encryptedPassword": "ENC"}),
            "/stremio/u-1/ENC/stream/movie/tt1.json": lambda q, h: (200, '{"streams": [{}, {}]}', "application/json"),
            "/stremio/u-1/ENC/stream/movie/tt2.json": lambda q, h: (200, '{"streams": []}', "application/json"),
            "/stremio/u-1/ENC/manifest.json": lambda q, h: (200, '{"id": "x", "name": "AIOStreams", "catalogs": [{"id": "a"}, {"id": "b"}]}', "application/json"),
        })
        self.api = AioClient(self.srv.url)

    def tearDown(self):
        self.srv.close()

    def test_healthy(self):
        self.assertTrue(self.api.healthy())
        self.assertFalse(AioClient(f"http://127.0.0.1:{closed_port()}").healthy())

    def test_starter_template(self):
        self.assertEqual(self.api.starter_template(), {"a": 1})

    def test_create_get_update(self):
        uuid, enc = self.api.create_user({"presets": [1]}, "pw")
        self.assertEqual((uuid, enc), ("u-1", "ENC"))
        self.assertEqual(self.api.get_user(uuid, "pw"), {"presets": [1]})
        self.api.update_user(uuid, "pw", {"presets": [2]})
        self.assertEqual(self.saved["config"], {"presets": [2]})

    def test_delete_user(self):
        with self.assertRaises(AioError):
            self.api.delete_user("u-1", "wrong")
        self.api.delete_user("u-1", "pw")
        self.assertEqual(self.saved, {"deleted": True})

    def test_validation_error_message_is_passed_on(self):
        with self.assertRaises(AioError) as cm:
            self.api.create_user({"presets": []}, "pw")
        self.assertEqual(cm.exception.code, "USER_INVALID_CONFIG")
        self.assertIn("At least one addon", str(cm.exception))

    def test_wrong_password(self):
        with self.assertRaises(AioError) as cm:
            self.api.update_user("u-1", "wrong", {})
        self.assertNotIn("wrong", str(cm.exception))

    def test_stream_count(self):
        self.assertEqual(self.api.stream_count("u-1", "ENC", "movie", "tt1"), 2)
        self.assertEqual(self.api.stream_count("u-1", "ENC", "movie", "tt2"), 0)
        self.assertEqual(AioClient(f"http://127.0.0.1:{closed_port()}").stream_count("u", "E", "movie", "tt1"), 0)

    def test_manifest_check_and_url(self):
        self.assertTrue(self.api.manifest_ok("u-1", "ENC"))
        self.assertFalse(self.api.manifest_ok("u-2", "ENC"))
        self.assertEqual(self.api.catalog_count("u-1", "ENC"), 2)
        self.assertEqual(self.api.catalog_count("u-2", "ENC"), 0)
        self.assertEqual(AioClient.addon_url("https://aiostreams.example.duckdns.org", "u-1", "ENC"),
                         "https://aiostreams.example.duckdns.org/stremio/u-1/ENC/manifest.json")
