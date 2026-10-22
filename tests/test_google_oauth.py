"""Astra-owned Google OAuth: direct callback, durable refresh, no cloud broker."""

from __future__ import annotations

import urllib.parse

import pytest
from fastapi.testclient import TestClient

from coworker.config import Config
from coworker.google_oauth import (
    _pending,
    begin_gmail_connect,
    complete_gmail_connect,
    refresh_gmail_token,
)
from coworker.secrets import SecretStore
from coworker.server import SessionManager, create_app


class _Response:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


def _config() -> Config:
    return Config(
        google_oauth_client_id="client-id.apps.googleusercontent.com",
        google_oauth_client_secret="client-secret",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    with TestClient(create_app(manager)) as test_client:
        yield test_client


def test_begin_gmail_connect_requires_astra_google_credentials():
    assert not begin_gmail_connect(Config())["ok"]


def test_begin_gmail_connect_uses_pkce_and_exact_callback():
    _pending.clear()
    out = begin_gmail_connect(_config())
    assert out["ok"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(out["authorize_url"]).query)
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/oauth/google/callback"]
    assert query["access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]
    assert "https://www.googleapis.com/auth/gmail.readonly" in query["scope"][0]
    assert query["state"][0] in _pending


def test_complete_gmail_connect_stores_refreshable_local_profile(tmp_path, monkeypatch):
    _pending.clear()
    store = SecretStore(tmp_path / "secrets.json")
    out = begin_gmail_connect(_config())
    state = urllib.parse.parse_qs(urllib.parse.urlparse(out["authorize_url"]).query)["state"][0]
    calls = []

    def post(url, *, data, timeout):
        calls.append((url, data))
        return _Response(
            200,
            {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
                "scope": "openid email gmail.readonly gmail.send",
            },
        )

    monkeypatch.setattr("coworker.google_oauth.httpx.post", post)
    monkeypatch.setattr(
        "coworker.google_oauth.httpx.get",
        lambda url, *, headers, timeout: _Response(200, {"email": "Me@Example.com"}),
    )

    result = complete_gmail_connect(store, _config(), code="one-time-code", state=state)
    assert result == {"ok": True, "account": "me@example.com"}
    assert calls[0][1]["code_verifier"]
    profile = store.get("gmail:account:me@example.com")
    assert profile["access_token"] == "access-1"
    assert profile["refresh_token"] == "refresh-1"
    assert profile["oauth_provider"] == "google"
    assert profile["managed"] is False
    assert store.get("gmail:default")["default_account"] == "me@example.com"
    assert not complete_gmail_connect(store, _config(), code="again", state=state)["ok"]


def test_refresh_gmail_token_updates_the_profile(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets.json")
    key = "gmail:account:me@example.com"
    store.put(
        key,
        {
            "oauth_provider": "google",
            "access_token": "old-access",
            "refresh_token": "refresh-1",
            "expires": 1,
        },
    )
    monkeypatch.setattr(
        "coworker.google_oauth.httpx.post",
        lambda url, *, data, timeout: _Response(
            200, {"access_token": "fresh-access", "expires_in": 3600}
        ),
    )

    profile = refresh_gmail_token(store, _config(), profile_key=key)
    assert profile is not None
    assert profile["access_token"] == "fresh-access"
    assert profile["refresh_token"] == "refresh-1"
    assert profile["expires"] > 1


def test_google_oauth_server_routes_use_astra_owned_flow(client, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("coworker.config.load_config", lambda *args, **kwargs: _config())
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    started = client.post("/v1/connectors/gmail/connect-google")
    assert started.status_code == 200
    assert started.json()["ok"] is True
    assert opened == [started.json()["authorize_url"]]
    assert opened[0].startswith("https://accounts.google.com/")

    monkeypatch.setattr(
        "coworker.google_oauth.complete_gmail_connect",
        lambda *args, **kwargs: {"ok": True, "account": "me@example.com"},
    )
    complete = client.get("/oauth/google/callback", params={"code": "code", "state": "state"})
    assert complete.status_code == 200
    assert "Gmail connected" in complete.text
    assert "return to Astra" in complete.text


def test_google_oauth_setup_route_opens_google_cloud(client, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("coworker.config.load_config", lambda *args, **kwargs: _config())
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    response = client.post("/v1/connectors/gmail/open-google-oauth-setup")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "setup_url": "https://console.cloud.google.com/apis/credentials",
        "redirect_uri": "http://127.0.0.1:8765/oauth/google/callback",
    }
    assert opened == ["https://console.cloud.google.com/apis/credentials"]
