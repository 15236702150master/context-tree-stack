from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

try:
    from sidecar import install_nginx_include
    from sidecar import server
except ModuleNotFoundError:  # Allows discovery from inside the sidecar directory.
    import install_nginx_include
    import server


class SidecarHelpersTests(unittest.TestCase):
    def test_limits_are_clamped_and_invalid_values_use_fallback(self) -> None:
        self.assertEqual(server.clamp_limit("25", 5, 100), 25)
        self.assertEqual(server.clamp_limit("999", 5, 100), 100)
        self.assertEqual(server.clamp_limit("bad", 5, 100), 5)
        self.assertEqual(server.clamp_limit("0", 5, 100), 5)

    def test_api_key_headers_support_bearer_and_x_api_key(self) -> None:
        self.assertEqual(server.extract_api_key({"Authorization": "Bearer abc"}), "abc")
        self.assertEqual(server.extract_api_key({"x-api-key": "xyz"}), "xyz")

    def test_nginx_installer_is_parameterized_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            conf = Path(directory) / "site.conf"
            conf.write_text("server {\n    location ^~ /v1/ {\n    }\n}\n", encoding="utf-8")
            include = "/etc/nginx/snippets/context-tree-usage-sidecar.locations.conf"
            backup = install_nginx_include.install(conf, include, "    location ^~ /v1/ {")
            self.assertIsNotNone(backup)
            result = conf.read_text(encoding="utf-8")
            self.assertEqual(result.count(f"include {include};"), 1)
            self.assertIsNone(install_nginx_include.install(conf, include, "    location ^~ /v1/ {"))


class SidecarHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.auth_patch = mock.patch.object(server, "authenticate", return_value={"user_id": 1, "api_key_id": 2})
        cls.sessions_patch = mock.patch.object(server, "sessions_payload", return_value={"mode": "real", "sessions": []})
        cls.requests_patch = mock.patch.object(server, "requests_payload", return_value={"mode": "real", "requests": []})
        cls.auth_patch.start()
        cls.sessions_patch.start()
        cls.requests_patch.start()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)
        cls.auth_patch.stop()
        cls.sessions_patch.stop()
        cls.requests_patch.stop()

    def request(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
        connection = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=2)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload

    def test_health_is_public(self) -> None:
        status, payload = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})

    def test_usage_route_requires_key_and_returns_json(self) -> None:
        status, _ = self.request("/v1/sub2api/usage/sessions", headers={"Authorization": "Bearer demo"})
        self.assertEqual(status, 200)
        status, payload = self.request("/v1/sub2api/usage/sessions/", headers={"Authorization": "Bearer demo"})
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["type"], "not_found_error")

    def test_invalid_key_gets_authentication_error(self) -> None:
        with mock.patch.object(server, "authenticate", return_value=None):
            status, payload = self.request("/v1/sub2api/usage/sessions")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["type"], "authentication_error")

    def test_invalid_session_id_is_rejected(self) -> None:
        oversized = "x" * 129
        status, payload = self.request(
            f"/v1/sub2api/usage/sessions/{oversized}/requests",
            headers={"x-api-key": "demo"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["type"], "invalid_request_error")


if __name__ == "__main__":
    unittest.main()
