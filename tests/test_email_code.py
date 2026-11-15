"""The Email code plugin: a code by mail signs in an ordinary client, and little else gets through."""

import os
import re
import shutil

import pytest
from fastapi.testclient import TestClient

from agentop import plugin_manifest
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import PluginHost
from tests.conftest import signed_in
from tests.fake_smtp import FakeSmtp
from tests.test_plugins import QuietStore

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = "/api/v2/plugins/email-code"


@pytest.fixture
def smtp():
    with FakeSmtp() as server:
        yield server


@pytest.fixture
def home(tmp_path, smtp):
    root = tmp_path / "plugins"
    shutil.copytree(plugin_manifest.DEFAULT_ROOT / "email-code", root / "email-code")
    data = tmp_path / "home"
    config = data / "plugins" / "email-code" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'to = "me@example.com"\nsmtp_host = "127.0.0.1"\nsmtp_port = {smtp.port}\nsmtp_security = "none"\n')
    os.chmod(config, 0o600)
    (data / "state").mkdir()
    (data / "state" / "plugins.json").write_text('{"email-code": {"enabled": true}}')
    return root, data, config


@pytest.fixture
def client(home):
    root, data, _ = home
    store = QuietStore()
    host = PluginHost(store, roots=[root], data_dir=data)
    app = create_app(Settings(), Identity("service-token", "iid", "test"), store, host, clients=signed_in(TOKEN))
    with TestClient(app) as c:
        yield c


def code_in(mail) -> str:
    return re.search(r"\b(\d{6})\b", mail["Subject"])[1]


def test_it_is_a_login_method_once_enabled(client):
    assert {"id": "email-code", "name": "Email code"} in client.get("/api/v2/auth/methods").json()


def test_off_by_default(tmp_path):
    manifest = plugin_manifest.read_manifest(plugin_manifest.DEFAULT_ROOT / "email-code")
    assert manifest.default_enabled is False and manifest.ui == ("login",)


def test_a_mailed_code_signs_in_an_ordinary_client(client, smtp):
    sent = client.post(f"{BASE}/send", json={"name": "Chrome on Android"})
    assert sent.status_code == 200
    body = sent.json()
    assert set(body) == {"request", "expires_in"} and body["expires_in"] == 600
    [mail] = smtp.mails
    assert mail["To"] == "me@example.com" and "Chrome on Android" in mail.get_content()
    signed = client.post(f"{BASE}/verify", json={"request": body["request"], "code": code_in(mail), "name": "Chrome on Android"})
    assert signed.status_code == 200
    mine = {"Authorization": f"Bearer {signed.json()['token']}"}
    assert client.get("/api/v2/sessions", headers=mine).status_code == 200
    listed = client.get("/api/v2/auth/clients", headers=mine).json()
    assert [c["name"] for c in listed if c["current"]] == ["Chrome on Android"]
    # Single use.
    again = client.post(f"{BASE}/verify", json={"request": body["request"], "code": code_in(mail)})
    assert again.status_code == 401


def test_a_code_works_only_with_the_request_that_asked_for_it(client, smtp):
    client.post(f"{BASE}/send", json={})
    code = code_in(smtp.mails[0])
    assert client.post(f"{BASE}/verify", json={"request": "someone-else", "code": code}).status_code == 401


def test_five_wrong_codes_end_the_request(client, smtp):
    request = client.post(f"{BASE}/send", json={}).json()["request"]
    code = code_in(smtp.mails[0])
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert client.post(f"{BASE}/verify", json={"request": request, "code": wrong}).status_code == 401
    assert client.post(f"{BASE}/verify", json={"request": request, "code": code}).status_code == 401


def test_asking_again_at_once_is_refused_and_sends_nothing(client, smtp):
    assert client.post(f"{BASE}/send", json={}).status_code == 200
    again = client.post(f"{BASE}/send", json={})
    assert again.status_code == 429 and int(again.headers["Retry-After"]) > 0
    assert len(smtp.mails) == 1


def test_not_set_up_says_so_and_nothing_more(client, home):
    home[2].unlink()
    response = client.post(f"{BASE}/send", json={})
    assert response.status_code == 503 and "me@example.com" not in response.text


def test_a_mail_that_cant_be_sent_says_so_without_details(client, home):
    home[2].write_text('to = "me@example.com"\nsmtp_host = "127.0.0.1"\nsmtp_port = 1\nsmtp_security = "none"\n')
    response = client.post(f"{BASE}/send", json={})
    assert response.status_code == 502 and "127.0.0.1" not in response.text
    # A failed send doesn't leave a code to guess.
    assert client.post(f"{BASE}/verify", json={"request": "x", "code": "123456"}).status_code == 401


def test_turned_off_it_is_no_way_in(client):
    client.patch("/api/v2/plugins/email-code", json={"enabled": False}, headers=AUTH)
    assert client.post(f"{BASE}/send", json={}).status_code == 404
    assert [m["id"] for m in client.get("/api/v2/auth/methods").json()] == ["token"]


def _module(name):
    import importlib.util
    import sys

    path = plugin_manifest.DEFAULT_ROOT / "email-code" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"email_code_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


codes_mod = _module("codes")
mail_mod = _module("mail")


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_a_code_runs_out_after_ten_minutes():
    clock = Clock()
    codes = codes_mod.Codes(clock)
    request, code = codes.issue()
    clock.now += 599
    assert codes.check(request, " ".join(code))  # spaces typed in are fine
    clock.now += 60
    request, code = codes.issue()
    clock.now += 601
    assert not codes.check(request, code)


def test_only_five_codes_an_hour_and_a_new_one_replaces_the_last():
    clock = Clock()
    codes = codes_mod.Codes(clock)
    first, first_code = codes.issue()
    for _ in range(4):
        clock.now += 61
        codes.issue()
    clock.now += 61
    with pytest.raises(codes_mod.TooSoon):
        codes.issue()
    assert not codes.check(first, first_code)
    clock.now += 3600
    codes.issue()


@pytest.mark.parametrize(
    "toml, error",
    [
        ("", "'to' is not set"),
        ('to = "me"\nsmtp_host = "h"', "not an email address"),
        ('to = "me@x"', "'smtp_host' is not set"),
        ('to = "me@x"\nsmtp_host = "smtp.example.com"\nsmtp_security = "none"', "only for a mail server on this machine"),
        ('to = "me@x"\nsmtp_host = "h"\nsmtp_user = "u"', "set both"),
        ('to = "me@x"\nsmtp_host = "h"\nsmtp_port = "587"', "port number"),
    ],
)
def test_settings_are_checked(tmp_path, toml, error):
    (tmp_path / "config.toml").write_text(toml)
    with pytest.raises(mail_mod.ConfigError, match=error):
        mail_mod.load(tmp_path)


def test_settings_defaults(tmp_path):
    (tmp_path / "config.toml").write_text('to = "me@x"\nsmtp_host = "smtp.gmail.com"\nsmtp_port = 465\nsmtp_user = "u@gmail.com"\nsmtp_password = "p"')
    config = mail_mod.load(tmp_path)
    assert (config.security, config.sender) == ("ssl", "u@gmail.com")
