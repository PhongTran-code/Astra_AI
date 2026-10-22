"""OpenCode Zen provider with an explicit free-model allowlist.

Zen exposes most models through OpenAI-compatible Chat Completions, while the
free Muse Spark contributor model uses the Responses API. Keeping both clients
behind one provider lets the model picker show one Zen entry without allowing
an arbitrary paid Zen model to be selected accidentally.
"""

from __future__ import annotations

from typing import Any, Optional

from .base import AssistantTurn, ModelCapabilities, ProviderClient
from .openai_provider import OpenAIProvider
from .openai_responses import OpenAIResponsesProvider

OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"

# This is deliberately an allowlist, not a prefix check. Zen's free catalog is
# temporary and can change; adding a paid model must require an explicit code
# change rather than becoming available through a manual model id.
OPENCODE_ZEN_FREE_MODELS = (
    "big-pickle",
    "mimo-v2.5-free",
    "ling-3.0-flash-fin-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "muse-spark-1.2-contributor-free",
    "muse-spark-1.3-contributor-free",
    "laguna-s-2.1-free",
    "deepseek-v4-flash-free",
)
OPENCODE_ZEN_FREE_MODEL_SET = frozenset(OPENCODE_ZEN_FREE_MODELS)
OPENCODE_ZEN_RESPONSES_MODELS = frozenset({
    "muse-spark-1.2-contributor-free",
    "muse-spark-1.3-contributor-free",
})
OPENCODE_CLIENT_HEADERS = {"User-Agent": "opencode"}


class OpenCodeZenProvider(ProviderClient):
    """Dispatch the allowlisted Zen free models to their documented wire API.

    The endpoint accepts free-tier requests anonymously. ``public`` is only an SDK construction
    placeholder; it is not a credential and is never persisted or shown to the user.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = OPENCODE_ZEN_BASE_URL,
        chat_client: Optional[ProviderClient] = None,
        responses_client: Optional[ProviderClient] = None,
    ) -> None:
        base = base_url.strip().rstrip("/")
        self._api_key = api_key
        self._base_url = base
        sdk_key = api_key or "public"
        self._chat = chat_client or OpenAIProvider(
            api_key=sdk_key,
            base_url=base,
            default_headers=OPENCODE_CLIENT_HEADERS,
        )
        self._responses = responses_client or OpenAIResponsesProvider(
            api_key=sdk_key,
            base_url=base,
            default_headers=OPENCODE_CLIENT_HEADERS,
        )

    @staticmethod
    def _validate_model(model: str) -> None:
        if model not in OPENCODE_ZEN_FREE_MODEL_SET:
            raise RuntimeError(
                f"OpenCode Zen free tier does not include {model!r}. "
                "Select one of the listed free models."
            )

    def _client_for(self, model: str) -> ProviderClient:
        self._validate_model(model)
        if model in OPENCODE_ZEN_RESPONSES_MODELS:
            return self._responses
        return self._chat

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ) -> AssistantTurn:
        return self._client_for(model).complete(
            model=model, messages=messages, tools=tools, **settings
        )

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ):
        yield from self._client_for(model).stream(
            model=model, messages=messages, tools=tools, **settings
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        self._validate_model(model)
        return ModelCapabilities(
            tools=True,
            vision=False,
            parallel_tool_calls=True,
            streaming=True,
        )
