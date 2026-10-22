"""Tests for Remote & SSH Runner server routes."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


class DummyProvider(ProviderClient):
    def complete(self, **kwargs):
        return None

    def capabilities(self, model):
        return ModelCapabilities()


def _client(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=DummyProvider())
    return TestClient(create_app(manager))


def test_remote_ssh_profiles_crud(tmp_path):
    client = _client(tmp_path)

    # Initially empty
    resp = client.get("/v1/remote/ssh/profiles")
    assert resp.status_code == 200
    assert resp.json()["profiles"] == []

    # Create profile
    payload = {
        "name": "Production VPS",
        "host": "192.168.1.50",
        "port": 22,
        "username": "root",
        "password": "secret_password",
        "remote_cwd": "/var/www/app",
    }
    save_resp = client.post("/v1/remote/ssh/profiles", json=payload)
    assert save_resp.status_code == 200
    res = save_resp.json()
    assert res["ok"] is True
    profile_id = res["profile"]["id"]
    assert profile_id.startswith("ssh_")
    assert res["profile"]["has_password"] is True
    assert "secret_password" not in str(res["profile"])

    # List profiles
    list_resp = client.get("/v1/remote/ssh/profiles")
    profiles = list_resp.json()["profiles"]
    assert len(profiles) == 1
    assert profiles[0]["id"] == profile_id
    assert profiles[0]["name"] == "Production VPS"

    # Set active profile
    act_resp = client.post("/v1/remote/ssh/active", json={"profile_id": profile_id})
    assert act_resp.status_code == 200
    assert act_resp.json()["active_ssh_profile"] == profile_id

    # Check remote status
    status_resp = client.get("/v1/remote/status")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["active_ssh_profile"]["id"] == profile_id
    assert status["ssh_profiles_count"] == 1

    # Delete profile
    del_resp = client.delete(f"/v1/remote/ssh/profiles/{profile_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True

    # List again
    list_again = client.get("/v1/remote/ssh/profiles").json()
    assert list_again["profiles"] == []
