import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.http import HttpError, RequestsHttp


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/secret")
            self.end_headers()
        elif self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(b"x" * 300_000)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(b"ok")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        out = json.dumps({"echo": body, "auth": self.headers.get("Authorization")}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def base():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_get_post_and_headers(base):
    h = RequestsHttp()
    r = h.request("GET", base + "/x")
    assert r.ok and r.body == b"ok" and r.headers["content-type"] == "image/jpeg"
    r = h.request("POST", base + "/p", headers={"Authorization": "Bearer T"}, json={"a": 1})
    assert r.json() == {"echo": {"a": 1}, "auth": "Bearer T"}


def test_redirects_are_never_followed(base):
    r = RequestsHttp().request("GET", base + "/redirect")
    assert r.status == 302 and not r.ok


def test_max_bytes_enforced(base):
    h = RequestsHttp()
    assert len(h.request("GET", base + "/big", max_bytes=400_000).body) == 300_000
    with pytest.raises(HttpError):
        h.request("GET", base + "/big", max_bytes=100_000)


def test_network_error_is_retryable():
    with pytest.raises(HttpError) as e:
        RequestsHttp().request("GET", "http://127.0.0.1:1/", timeout=2)
    assert e.value.retryable
