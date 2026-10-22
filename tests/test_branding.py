from __future__ import annotations

from coworker.branding import env_flag, getenv, legacy_env_name


def test_astra_environment_flags(monkeypatch):
    monkeypatch.setenv("ASTRA_UNSHIPPED", "1")
    assert getenv("ASTRA_UNSHIPPED") == "1"
    assert env_flag("ASTRA_UNSHIPPED") is True

    monkeypatch.setenv("ASTRA_UNSHIPPED", "0")
    assert getenv("ASTRA_UNSHIPPED") == "0"
    assert env_flag("ASTRA_UNSHIPPED") is False
