"""Credential-free integration tests against the actual Docker image."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from local_stubs import Stubs

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("EDGE_TEST_IMAGE", "caddy-railway:hermes-only-test")
ENV = {
    "AUTH_HOST": "auth.example.test",
    "HERMES_HOST": "hermes.example.test",
    "HERMES_TAILSCALE_HOST": "hermes.private.test",
    "AUTHELIA_UPSTREAM": "host.docker.internal:19091",
    "TAILSCALE_PROXY_URL": "http://host.docker.internal:11055",
}


def docker(*args, check=True):
    return subprocess.run(["docker", *args], cwd=ROOT, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


class EdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = docker("build", "-t", IMAGE, ".")
        print(result.stdout, result.stderr)

    def setUp(self):
        self.stubs = Stubs()
        self.addCleanup(self.stubs.close)
        self.env = {
            **ENV,
            "AUTHELIA_UPSTREAM": f"host.docker.internal:{self.stubs.auth_port}",
            "TAILSCALE_PROXY_URL": f"http://host.docker.internal:{self.stubs.proxy_port}",
            "SSL_CERT_FILE": "/test-cert.pem",
        }

    def start_edge(self, env=None):
        env = self.env if env is None else env
        listen_port = env.get("PORT", "8080")
        args = ["run", "--detach", "--publish", f"127.0.0.1::{listen_port}",
                "--mount", f"type=bind,source={self.stubs.cert},target=/test-cert.pem,readonly"]
        for key, value in env.items():
            args.extend(["--env", f"{key}={value}"])
        container = docker(*args, IMAGE).stdout.strip()
        self.addCleanup(lambda: docker("rm", "--force", container))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            details = json.loads(docker("inspect", container).stdout)[0]
            if not details["State"]["Running"]:
                self.fail("Caddy exited: " + docker("logs", container).stderr)
            try:
                self.port = int(details["NetworkSettings"]["Ports"][f"{listen_port}/tcp"][0]["HostPort"])
                if self.request("unknown.example.test", "/healthz")[0] == 200:
                    return container
            except (KeyError, TypeError, OSError, http.client.HTTPException):
                pass
            time.sleep(0.1)
        self.fail("Caddy never became healthy: " + docker("logs", container).stderr)

    def request(self, host, path="/", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path, headers={"Host": host, **(headers or {})})
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_legacy_comfy_variables_cannot_enable_route(self):
        self.start_edge({**self.env, "COMFY_HOST": "comfy.example.test",
                         "COMFY_TAILSCALE_HOST": "comfy.private.test"})
        self.assertEqual(self.request("comfy.example.test")[0], 404)

    def test_hermes_only_environment_enables_edge(self):
        self.start_edge()
        self.assertEqual(self.request("unknown.example.test")[0], 404)
        self.assertEqual(self.request("comfy.example.test")[0], 404)

    def test_portal_preserves_railway_client_ip(self):
        self.start_edge()
        status, body, _ = self.request(ENV["AUTH_HOST"], "/login", {
            "X-Real-IP": "192.0.2.25", "X-Forwarded-For": "198.51.100.99"})
        self.assertEqual((status, body), (200, b"portal"))
        path, headers = self.stubs.auth_requests[-1]
        self.assertEqual(path, "/login")
        self.assertEqual(headers["X-Forwarded-For"], "192.0.2.25")
        self.assertEqual(headers["X-Forwarded-Proto"], "https")
        self.assertEqual(self.stubs.connects, [])

    def test_authorized_https_backhaul_default_port(self):
        self.check_authorized_backhaul()

    def test_authorized_https_backhaul_optional_port(self):
        self.check_authorized_backhaul("8443")

    def check_authorized_backhaul(self, port=None):
        env = dict(self.env)
        if port:
            env["HERMES_TAILSCALE_PORT"] = port
        self.start_edge(env)
        status, body, _ = self.request(ENV["HERMES_HOST"], "/api/status?check=1", {
            "X-Real-IP": "192.0.2.25", "X-Forwarded-For": "198.51.100.99"})
        self.assertEqual(status, 200, body)
        app = json.loads(body)
        self.assertEqual(app["path"], "/api/status?check=1")
        self.assertEqual(app["headers"]["Host"], ENV["HERMES_HOST"])
        self.assertEqual(app["headers"]["X-Forwarded-Proto"], "https")
        self.assertEqual(self.stubs.connects, [f"hermes.private.test:{port or '443'}"])
        path, auth = self.stubs.auth_requests[-1]
        self.assertEqual(path, "/api/authz/forward-auth?check=1")
        self.assertEqual(auth["X-Forwarded-For"], "192.0.2.25")
        self.assertEqual(auth["X-Forwarded-Proto"], "https")
        self.assertEqual(auth["X-Forwarded-Method"], "GET")
        self.assertEqual(auth["X-Forwarded-Uri"], "/api/status?check=1")

    def test_auth_denials_redirects_and_errors_never_touch_backhaul(self):
        self.start_edge()
        for status in (401, 403, 302, 500, 503):
            self.stubs.auth_status = status
            for path in ("/", "/api/status", "/api/status?check=1", "/assets/app.js", "/ws"):
                with self.subTest(status=status, path=path):
                    actual, _, headers = self.request(ENV["HERMES_HOST"], path)
                    self.assertEqual(actual, status)
                    if status == 302:
                        self.assertEqual(headers["Location"], "https://auth.example.test/")
        self.assertEqual(len(self.stubs.auth_requests), 25)
        self.assertEqual(self.stubs.connects, [])
        self.assertEqual(self.stubs.app_requests, [])

    def test_auth_connection_failure_is_fail_closed(self):
        self.start_edge({**self.env, "AUTHELIA_UPSTREAM": "127.0.0.1:1"})
        self.assertEqual(self.request(ENV["HERMES_HOST"])[0], 502)
        self.assertEqual(self.stubs.connects, [])
        self.assertEqual(self.stubs.app_requests, [])

    def test_backhaul_rejects_wrong_tls_hostname(self):
        self.start_edge({**self.env, "HERMES_TAILSCALE_HOST": "wrong.private.test"})
        self.assertEqual(self.request(ENV["HERMES_HOST"])[0], 502)
        self.assertEqual(self.stubs.connects, ["wrong.private.test:443"])
        self.assertEqual(self.stubs.app_requests, [])

    def test_websocket_upgrade_remains_authenticated(self):
        self.start_edge()
        headers = {"Upgrade": "websocket", "Connection": "Upgrade",
                   "Origin": "https://hermes.example.test"}
        self.stubs.auth_status = 403
        self.assertEqual(self.request(ENV["HERMES_HOST"], "/ws", headers)[0], 403)
        self.assertEqual(self.stubs.connects, [])
        self.stubs.auth_status = 204
        self.assertEqual(self.request(ENV["HERMES_HOST"], "/ws", headers)[0], 101)
        self.assertEqual(self.stubs.app_requests[-1][1]["Origin"], headers["Origin"])

    def test_each_missing_required_variable_uses_maintenance(self):
        for key in ENV:
            with self.subTest(missing=key):
                env = dict(self.env)
                del env[key]
                container = self.start_edge(env)
                self.assertEqual(self.request(ENV["HERMES_HOST"])[0], 503)
                logs = docker("logs", container).stderr
                self.assertIn(key, logs)
                self.assertNotIn(env.get("TAILSCALE_PROXY_URL", "unused-value"), logs)
        self.assertEqual(self.stubs.auth_requests, [])
        self.assertEqual(self.stubs.connects, [])

    def test_duplicate_portal_and_hermes_use_maintenance(self):
        self.start_edge({**self.env, "AUTH_HOST": ENV["HERMES_HOST"]})
        self.assertEqual(self.request(ENV["HERMES_HOST"])[0], 503)
        self.assertEqual(self.stubs.auth_requests, [])


    def test_production_listen_port_443(self):
        self.start_edge({**self.env, "PORT": "443"})
        self.assertEqual(self.request("comfy.example.test")[0], 404)
        self.assertEqual(self.request(ENV["HERMES_HOST"])[0], 200)

    def test_empty_required_variables_use_maintenance(self):
        self.start_edge({**self.env, **{key: "" for key in ENV}})
        self.assertEqual(self.request(ENV["AUTH_HOST"])[0], 503)
        self.assertEqual(self.request("unknown.example.test")[0], 503)
        self.assertEqual(self.stubs.auth_requests, [])

    def test_health_and_unknown_hosts_do_not_touch_upstreams(self):
        self.start_edge()
        for host in (ENV["AUTH_HOST"], ENV["HERMES_HOST"], "unknown.example.test", "comfy.example.test"):
            with self.subTest(host=host):
                self.assertEqual(self.request(host, "/healthz")[:2], (200, b"ok"))
        for host in ("unknown.example.test", "comfy.example.test", "generated.up.railway.app"):
            self.assertEqual(self.request(host)[0], 404)
        self.assertEqual(self.stubs.auth_requests, [])
        self.assertEqual(self.stubs.connects, [])

    def test_actual_caddy_config_validation(self):
        for port in (None, "8443"):
            env = dict(self.env)
            if port:
                env["HERMES_TAILSCALE_PORT"] = port
            args = ["run", "--rm", "--entrypoint", "caddy"]
            for key, value in env.items():
                args.extend(["--env", f"{key}={value}"])
            result = docker(*args, IMAGE, "adapt", "--config", "/etc/caddy/Caddyfile",
                            "--adapter", "caddyfile", "--validate")
            config = json.loads(result.stdout)
            self.assertIn(f"hermes.private.test:{port or '443'}", json.dumps(config))
            self.assertNotIn("comfy", json.dumps(config).lower())
        result = docker("run", "--rm", "--entrypoint", "caddy", IMAGE,
                        "validate", "--config", "/etc/caddy/Caddyfile.maintenance",
                        "--adapter", "caddyfile")
        self.assertIn("Valid configuration", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
