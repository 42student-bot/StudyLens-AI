import hashlib
import hmac
import json
import socket
import threading
import time

import pytest
import requests
import uvicorn

from app.ai.mock import MockProvider
from app.budget import Budget
from app.config import Settings
from app.http import HttpResponse
from app.main import create_app
from app.messenger import MessengerClient
from app.orchestrator import Orchestrator
from app.store import Store

from conftest import FakeHttp, make_page_jpeg

SECRET = "app-secret"
PSID = "42"
CDN = "https://scontent.xx.fbcdn.net/page.jpg"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def server(tmp_path):
    s = Settings(
        data_dir=str(tmp_path), fb_app_secret=SECRET, fb_verify_token="vtoken", fb_page_token="tok",
        allowed_psids=frozenset({PSID}), auto_analyze_delay=0.05, budget_daily=100.0, budget_monthly=100.0,
    )
    http = FakeHttp()
    http.when(lambda m, u: "graph.facebook.com" in u, HttpResponse(200, b"{}"))
    http.when(lambda m, u: u == CDN, HttpResponse(200, make_page_jpeg(True), {"content-type": "image/jpeg"}))
    store = Store(str(tmp_path))
    orch = Orchestrator(s, store, MessengerClient(s, http), [MockProvider()], Budget(s, store))
    app = create_app(s, http, orch, store)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started
    yield f"http://127.0.0.1:{port}", http, store
    srv.should_exit = True
    th.join(timeout=5)


def event(mid, text="", urls=()):
    msg = {"mid": mid}
    if text:
        msg["text"] = text
    if urls:
        msg["attachments"] = [{"type": "image", "payload": {"url": u}} for u in urls]
    return {"object": "page", "entry": [{"messaging": [{"sender": {"id": PSID}, "message": msg}]}]}


def post(base, payload, signature="auto"):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers["X-Hub-Signature-256"] = sign(body)
    elif signature:
        headers["X-Hub-Signature-256"] = signature
    return requests.post(base + "/webhook", data=body, headers=headers, timeout=5)


def test_health(server):
    base, _, _ = server
    r = requests.get(base + "/healthz", timeout=5)
    assert r.status_code == 200 and r.json() == {"status": "ok", "providers": ["mock"]}


def test_webhook_verification_handshake(server):
    base, _, _ = server
    ok = requests.get(base + "/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "vtoken", "hub.challenge": "12345"}, timeout=5)
    assert ok.status_code == 200 and ok.text == "12345"
    for params in (
        {"hub.mode": "subscribe", "hub.verify_token": "mauvais", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.challenge": "1"},
        {},
    ):
        assert requests.get(base + "/webhook", params=params, timeout=5).status_code == 403


def test_post_rejects_bad_or_missing_signature_and_bad_input(server):
    base, http, _ = server
    assert post(base, event("m1", "aide"), signature="sha256=" + "0" * 64).status_code == 403
    assert post(base, event("m2", "aide"), signature=None).status_code == 403
    body = b"pas du json"
    r = requests.post(base + "/webhook", data=body, headers={"X-Hub-Signature-256": sign(body)}, timeout=5)
    assert r.status_code == 400
    big = b"x" * 1_100_000
    r = requests.post(base + "/webhook", data=big, headers={"X-Hub-Signature-256": sign(big)}, timeout=5)
    assert r.status_code == 413
    time.sleep(0.2)
    assert not [r for r in http.requests if "graph.facebook.com" in r["url"]]  # rien n'a été traité


def test_signed_webhook_end_to_end_photo_to_answers(server):
    base, http, store = server
    r = post(base, event("m10", urls=[CDN]))
    assert r.status_code == 200 and r.text == "EVENT_RECEIVED"
    deadline = time.time() + 10
    texts = []
    while time.time() < deadline:
        s = store.latest_session(PSID)
        texts = [
            r["json"]["message"]["text"]
            for r in http.requests
            if "graph.facebook.com" in r["url"]
        ]
        if s and s.state == "done" and "1 question(s) d?tect?e(s)" in texts:
            break
        time.sleep(0.05)

    texts = [
        r["json"]["message"]["text"]
        for r in http.requests
        if "graph.facebook.com" in r["url"]
    ]
    assert texts[0] == "Page 1 capturée"
    assert "1 question(s) détectée(s)" in texts
    assert any(t.startswith("Q1/1") for t in texts)
    # un second envoi du même webhook (retry de Meta) ne retraite rien
    n = len(texts)
    assert post(base, event("m10", urls=[CDN])).status_code == 200
    time.sleep(0.3)
    assert len([r for r in http.requests if "graph.facebook.com" in r["url"]]) == n
