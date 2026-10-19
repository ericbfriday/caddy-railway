"""Local-only fake Authelia, HTTPS app, and HTTP CONNECT backhaul.

These are protocol fixtures, not a substitute for live MFA/Tailscale testing.
The CONNECT fixture only tunnels to the test app, never arbitrary destinations.
"""
import http.server
import json
import os
from pathlib import Path
import select
import socket
import ssl
import subprocess
import tempfile
import threading


class Stubs:
    def __init__(self):
        self.auth_status = 204
        self.auth_requests = []
        self.app_requests = []
        self.connects = []
        self.servers = []
        self.threads = []
        self.temp = tempfile.TemporaryDirectory(prefix="caddy-edge-", dir=os.environ.get("TMPDIR"))
        directory = Path(self.temp.name)
        self.cert = directory / "cert.pem"
        key = directory / "key.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(self.cert), "-days", "1",
            "-subj", "/CN=hermes.private.test",
            "-addext", "subjectAltName=DNS:hermes.private.test",
            "-addext", "basicConstraints=critical,CA:TRUE",
        ], check=True, capture_output=True)
        owner = self

        class Quiet(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def send(self, status: int, body: bytes = b"", **headers: str):
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Auth(Quiet):
            def do_GET(self):
                owner.auth_requests.append((self.path, dict(self.headers)))
                if self.path.split("?", 1)[0] == "/api/authz/forward-auth":
                    headers = {"Location": "https://auth.example.test/"} if owner.auth_status == 302 else {}
                    self.send(owner.auth_status, **headers)
                else:
                    self.send(200, b"portal")

        class App(Quiet):
            def do_GET(self):
                owner.app_requests.append((self.path, dict(self.headers)))
                if self.headers.get("Upgrade", "").lower() == "websocket":
                    self.send_response(101)
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "Upgrade")
                    self.end_headers()
                else:
                    self.send(200, json.dumps({"path": self.path, "headers": dict(self.headers)}).encode())

        class Proxy(Quiet):
            def do_CONNECT(self):
                owner.connects.append(self.path)
                with socket.create_connection(("127.0.0.1", owner.app_port), timeout=5) as upstream:
                    self.send_response(200)
                    self.end_headers()
                    peers = [self.connection, upstream]
                    while True:
                        readable, _, _ = select.select(peers, [], [], 5)
                        if not readable:
                            return
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            (upstream if source is self.connection else self.connection).sendall(data)

        try:
            self.app_port = self.serve(App, cert=key)
            self.auth_port = self.serve(Auth)
            self.proxy_port = self.serve(Proxy)
        except BaseException:
            self.close()
            raise

    def serve(self, handler, cert=None):
        # Docker Desktop reaches these short-lived fixtures via host.docker.internal.
        server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), handler)
        server.daemon_threads = True
        if cert:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(self.cert), str(cert))
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.servers.append(server)
        self.threads.append(thread)
        thread.start()
        return server.server_port

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
        self.temp.cleanup()
