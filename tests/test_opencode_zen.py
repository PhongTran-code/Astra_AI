"""OpenCode Zen free-tier provider tests."""

from __future__ import annotations

import pytest
import httpx

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from coworker.providers.matrix import models_for_provider
from coworker.providers.opencode_zen import (
    OPENCODE_ZEN_BASE_URL,
    OPENCODE_ZEN_FREE_MODELS,
    OPENCODE_ZEN_RESPONSES_MODELS,
    OPENCODE_CLIENT_HEADERS,
    OpenCodeZenProvider,
)
from coworker.providers.registry import (
    build_provider_client,
    get_descriptor,
    verify_provider_key,
)


class _Recorder(ProviderClient):
    def __init__(self, label: str):
        self.label = label
        self.models: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        return AssistantTurn(text=self.label)

    def stream(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        yield StreamChunk(turn=AssistantTurn(text=self.label))

    def capabilities(self, model):
        return ModelCapabilities()


def test_zen_dispatches_chat_and_responses_free_models():
    chat = _Recorder("chat")
    responses = _Recorder("responses")
    provider = OpenCodeZenProvider(chat_client=chat, responses_client=responses)

    assert provider.complete(model="big-pickle", messages=[]).text == "chat"
    assert provider.complete(
        model="muse-spark-1.2-contributor-free", messages=[]
    ).text == "responses"
    assert provider.complete(
        model="muse-spark-1.3-contributor-free", messages=[]
    ).text == "responses"
    assert provider.complete(
        model="laguna-s-2.1-free", messages=[]
    ).text == "chat"
    assert chat.models == ["big-pickle", "laguna-s-2.1-free"]
    assert responses.models == [
        "muse-spark-1.2-contributor-free",
        "muse-spark-1.3-contributor-free",
    ]


def test_zen_rejects_models_outside_free_allowlist():
    provider = OpenCodeZenProvider(
        chat_client=_Recorder("chat"), responses_client=_Recorder("responses")
    )

    with pytest.raises(RuntimeError, match="free tier"):
        provider.complete(model="claude-sonnet-4-6", messages=[])


def test_zen_descriptor_and_matrix_are_free_only():
    descriptor = get_descriptor("opencode")
    assert descriptor is not None
    assert descriptor.name == "opencode"
    assert descriptor.needs_key is False
    assert descriptor.recommended_model == "big-pickle"
    assert set(models_for_provider("opencode")) == set(OPENCODE_ZEN_FREE_MODELS)
    assert get_descriptor("opencode-zen") is descriptor  # legacy alias
    assert OPENCODE_ZEN_RESPONSES_MODELS == {
        "muse-spark-1.2-contributor-free",
        "muse-spark-1.3-contributor-free",
    }


def test_zen_builder_is_keyless_but_accepts_an_optional_key(monkeypatch):
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    provider = build_provider_client("opencode", {}, None)
    assert isinstance(provider, OpenCodeZenProvider)
    assert provider._api_key is None

    provider = build_provider_client(
        "opencode",
        {"api_key": "zen-test-key"},
        None,
    )
    assert isinstance(provider, OpenCodeZenProvider)
    assert provider._api_key == "zen-test-key"


def test_zen_clients_identify_as_opencode(monkeypatch):
    captured: list[dict[str, object]] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    provider = OpenCodeZenProvider()

    provider._chat._ensure_client()
    provider._responses._ensure_client()

    assert len(captured) == 2
    assert all(item["default_headers"] == OPENCODE_CLIENT_HEADERS for item in captured)


def test_zen_keyless_probe_uses_public_models_endpoint(monkeypatch):
    calls: dict[str, object] = {}

    def fake_get(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    assert verify_provider_key("opencode-zen") == {"ok": True}
    assert calls["url"] == OPENCODE_ZEN_BASE_URL + "/models"
    assert calls["kwargs"]["headers"] == {}
