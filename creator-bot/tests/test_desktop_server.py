"""The real local HTTP server: what a browser (or a hostile web page) can and cannot do."""

import http.client
import json
import threading

import pytest

from creator_bot.config import Settings
from creator_bot.desktop.__main__ import running_instance
from creator_bot.desktop.app import DesktopApp
from creator_bot.desktop.runtime import Runtime
from creator_bot.desktop.server import DesktopServer
from creator_bot.storage import Database

from .conftest import FakeAutostart

TOKEN = "secret-token"


@pytest.fixture
def server():
    def create() -> DesktopApp:
        return DesktopApp(
            Settings(telegram_token="", owner_chat_id=1),
            Database(),
            show_os=lambda title, body: True,
            autostart=FakeAutostart(),
            open_url=lambda url: None,
        )

    runtime = Runtime(create, tick_seconds=3600)
    runtime.start()
    httpd = DesktopServer(("127.0.0.1", 0), runtime, TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    runtime.stop()


def fetch(server, method, path, body=None, headers=None, raw=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    headers = dict(headers or {})
    payload = raw
    if body is not None:
        payload = json.dumps(body)
        headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, payload, headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, dict(response.getheaders()), data


AUTH = {"X-Token": TOKEN}


def test_serves_the_app_with_the_token_and_strict_headers(server):
    status, headers, body = fetch(server, "GET", "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert f'content="{TOKEN}"'.encode() in body and b"__TOKEN__" not in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"

    assert fetch(server, "GET", "/app.js")[0] == 200
    assert fetch(server, "GET", "/app.css")[1]["Content-Type"].startswith("text/css")
    assert fetch(server, "GET", "/../config.py")[0] == 404
    assert fetch(server, "POST", "/")[0] == 405


def test_api_needs_the_token(server):
    assert fetch(server, "GET", "/api/today")[0] == 401
    assert fetch(server, "GET", "/api/today", headers={"X-Token": "wrong"})[0] == 401
    status, _, body = fetch(server, "GET", "/api/today", headers=AUTH)
    assert status == 200 and "quote" in json.loads(body)
    assert fetch(server, "GET", "/api/ping")[0] == 200  # public: lets a second launch find this instance


def test_other_host_names_are_rejected_to_stop_dns_rebinding(server):
    assert fetch(server, "GET", "/", headers={"Host": "evil.example"})[0] == 403
    assert fetch(server, "GET", "/api/today", headers={**AUTH, "Host": f"evil.example:{server.port}"})[0] == 403
    assert fetch(server, "GET", "/api/ping", headers={"Host": "evil.example"})[0] == 403
    assert fetch(server, "GET", "/api/ping", headers={"Host": f"localhost:{server.port}"})[0] == 200


def test_bodies_must_be_json_objects_of_reasonable_size(server):
    ok = fetch(server, "POST", "/api/ideas", {"text": "רעיון"}, AUTH)
    assert ok[0] == 201 and json.loads(ok[2])["text"] == "רעיון"
    assert (
        fetch(server, "POST", "/api/ideas", headers={**AUTH, "Content-Type": "text/plain"}, raw='{"text": "x"}')[0]
        == 400
    )
    assert (
        fetch(server, "POST", "/api/ideas", headers={**AUTH, "Content-Type": "application/json"}, raw="[1]")[0] == 400
    )
    assert (
        fetch(server, "POST", "/api/ideas", headers={**AUTH, "Content-Type": "application/json"}, raw="{bad")[0] == 400
    )
    assert (
        fetch(server, "POST", "/api/ideas", headers={**AUTH, "Content-Type": "application/json"}, raw="x" * 70_000)[0]
        == 400
    )


def test_full_round_trip_with_unicode(server):
    fetch(server, "POST", "/api/ideas", {"text": "סרטון על 🌮 טאקו"}, AUTH)
    _, _, body = fetch(server, "GET", "/api/ideas", headers=AUTH)
    assert json.loads(body)["ideas"][0]["text"] == "סרטון על 🌮 טאקו"
    assert fetch(server, "GET", "/api/ideas?x=1", headers=AUTH)[0] == 200
    assert fetch(server, "GET", "/api/missing", headers=AUTH)[0] == 404


def test_a_second_launch_can_find_the_running_instance(server):
    assert running_instance(server.port)


def test_nothing_running_on_a_free_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
    assert not running_instance(free_port)


def test_a_failing_startup_is_reported_instead_of_hanging():
    def broken() -> DesktopApp:
        raise RuntimeError("cannot open the database")

    runtime = Runtime(broken)
    with pytest.raises(RuntimeError, match="cannot open the database"):
        runtime.start()
