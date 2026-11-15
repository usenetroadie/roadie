"""Client for the HTTP API of the running AIOStreams instance (the only way the wizard
touches user configurations - never the database)."""

import base64
import json
import time
import urllib.error
import urllib.request

STARTER_TEMPLATE = "builtin.debrid-starter"  # AIOStreams' general starter template; only its defaults are used
# AIOStreams rate limits per IP: user API 5 per 5 s, stream API 5 per 10 s, creating a
# config (POST /api/v1/user) 10 per hour. On HTTP 429 every call waits and tries again.
RETRIES, RETRY_WAIT = 5, 6
CREATE_TRIES = 6  # POST /api/v1/user: tries in all
CREATE_WAIT = 10  # seconds between tries


class AioError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


class AioClient:
    def __init__(self, base_url: str, timeout: float = 60, sleep=time.sleep):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.sleep = sleep

    def _call(self, method: str, path: str, body=None, auth: str | None = None,
              retries: int = RETRIES, wait: float = RETRY_WAIT):
        for attempt in range(retries + 1):
            try:
                return self._call_once(method, path, body, auth)
            except AioError as e:
                limited = e.status == 429 or e.code == "RATE_LIMIT_EXCEEDED"
                if not limited or attempt == retries:
                    raise
            self.sleep(wait)

    def _call_once(self, method: str, path: str, body=None, auth: str | None = None):
        headers = {"Content-Type": "application/json", "User-Agent": "roadie"}
        if auth:
            headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        failure, status = None, None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        except (urllib.error.URLError, OSError):
            failure = AioError("UNREACHABLE", "AIOStreams did not answer")
        if failure:
            raise failure  # outside except: no chained exception with request details
        try:
            payload = json.loads(raw)
        except ValueError:
            raise AioError("BAD_ANSWER", "AIOStreams sent an unexpected answer", status) from None
        if isinstance(payload, dict) and payload.get("success") is False:
            error = payload.get("error") or {}
            raise AioError(error.get("code", "ERROR"), error.get("message") or payload.get("detail") or "", status)
        return payload

    def healthy(self) -> bool:
        try:
            self._call("GET", "/api/v1/health")
            return True
        except AioError:
            return False

    def starter_template(self) -> dict:
        templates = self._call("GET", "/api/v1/templates")["data"]
        for tpl in templates:
            if tpl.get("metadata", {}).get("id") == STARTER_TEMPLATE:
                return tpl["config"]
        raise AioError("NO_TEMPLATE", "starter template not found")

    def create_user(self, config: dict, password: str) -> tuple[str, str]:
        data = self._call("POST", "/api/v1/user", {"config": config, "password": password},
                          retries=CREATE_TRIES - 1, wait=CREATE_WAIT)["data"]
        return data["uuid"], data["encryptedPassword"]

    def get_user(self, uuid: str, password: str) -> dict:
        return self._call("GET", "/api/v1/user", auth=f"{uuid}:{password}")["data"]["userData"]

    def update_user(self, uuid: str, password: str, config: dict) -> None:
        self._call("PUT", "/api/v1/user", {"config": config}, auth=f"{uuid}:{password}")

    def delete_user(self, uuid: str, password: str) -> None:
        self._call("DELETE", "/api/v1/user", auth=f"{uuid}:{password}")

    def _manifest(self, uuid: str, encrypted_password: str) -> dict:
        try:
            manifest = self._call("GET", f"/stremio/{uuid}/{encrypted_password}/manifest.json")
        except AioError:
            return {}
        return manifest if isinstance(manifest, dict) else {}

    def manifest_ok(self, uuid: str, encrypted_password: str) -> bool:
        return "id" in self._manifest(uuid, encrypted_password)

    def catalog_count(self, uuid: str, encrypted_password: str) -> int:
        """Number of catalogs (rows for the app's start page and search); 0 on any error."""
        catalogs = self._manifest(uuid, encrypted_password).get("catalogs")
        return len(catalogs) if isinstance(catalogs, list) else 0

    def stream_count(self, uuid: str, encrypted: str, kind: str, imdb_id: str) -> int:
        """Number of streams the addon offers for one title; 0 on any error."""
        try:
            data = self._call("GET", f"/stremio/{uuid}/{encrypted}/stream/{kind}/{imdb_id}.json")
        except AioError:
            return 0
        streams = data.get("streams") if isinstance(data, dict) else None
        return len(streams) if isinstance(streams, list) else 0

    @staticmethod
    def addon_url(public_base: str, uuid: str, encrypted_password: str) -> str:
        return f"{public_base.rstrip('/')}/stremio/{uuid}/{encrypted_password}/manifest.json"
