from __future__ import annotations

import logging
import os
from typing import Any, Iterable

from amazon_lead_agent.llm.base import LLMClient
from amazon_lead_agent.llm.gemini_client import GeminiClient
from amazon_lead_agent.llm.minimax_client import MiniMaxClient

LOGGER = logging.getLogger(__name__)


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _split_providers(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.split(",")
    else:
        raw = list(value)
    result: list[str] = []
    for item in raw:
        name = str(item).strip().lower()
        if name and name not in result:
            result.append(name)
    return result


class LLMRouter:
    def __init__(
        self,
        provider: str | None = None,
        fallback_providers: str | Iterable[str] | None = None,
        config: dict[str, Any] | None = None,
        minimax_api_key: str | None = None,
        gemini_api_key: str | None = None,
    ) -> None:
        self.config = config or {}
        llm_config = self.config.get("llm", {}) if isinstance(self.config, dict) else {}
        self.primary_provider = (provider or _optional_env("LLM_PROVIDER") or llm_config.get("provider") or "minimax").strip().lower()
        fallback_value = fallback_providers if fallback_providers is not None else _optional_env("LLM_FALLBACK_PROVIDERS") or llm_config.get("fallback_providers") or "minimax,gemini"
        self.fallback_providers = _split_providers(fallback_value)
        self.minimax_api_key = minimax_api_key or _optional_env("MINIMAX_API_KEY")
        self.gemini_api_key = gemini_api_key or _optional_env("GEMINI_API_KEY")
        self.last_used_provider: str | None = None
        self.last_used_model: str | None = None
        self.last_attempted_providers: list[dict[str, str]] = []
        self._client_cache: dict[str, LLMClient | None] = {}

    def _ordered_providers(self) -> list[str]:
        ordered = [self.primary_provider, *self.fallback_providers]
        deduped: list[str] = []
        for name in ordered:
            if name and name not in deduped:
                deduped.append(name)
        return deduped

    def _build_minimax_client(self) -> MiniMaxClient:
        llm_config = self.config.get("llm", {}) if isinstance(self.config, dict) else {}
        return MiniMaxClient(
            api_key=self.minimax_api_key,
            api_style=_optional_env("MINIMAX_API_STYLE") or llm_config.get("minimax_api_style"),
            model=_optional_env("MINIMAX_MODEL") or llm_config.get("minimax_model"),
            api_base=_optional_env("MINIMAX_API_BASE") or llm_config.get("minimax_api_base"),
            fallback_model=_optional_env("MINIMAX_FALLBACK_MODEL") or llm_config.get("minimax_fallback_model"),
            fallback_api_style=_optional_env("MINIMAX_FALLBACK_API_STYLE") or llm_config.get("minimax_fallback_api_style"),
            fallback_api_base=_optional_env("MINIMAX_FALLBACK_API_BASE") or llm_config.get("minimax_fallback_api_base"),
            timeout_seconds=int(_optional_env("MINIMAX_TIMEOUT_SECONDS") or llm_config.get("minimax_timeout_seconds") or 90),
            max_tokens_discovery=int(_optional_env("MINIMAX_MAX_TOKENS_DISCOVERY") or llm_config.get("minimax_max_tokens_discovery") or 3000),
            max_tokens_research=int(_optional_env("MINIMAX_MAX_TOKENS_RESEARCH") or llm_config.get("minimax_max_tokens_research") or 2048),
        )

    def _build_gemini_client(self) -> GeminiClient:
        llm_config = self.config.get("llm", {}) if isinstance(self.config, dict) else {}
        return GeminiClient(
            api_key=self.gemini_api_key,
            model=_optional_env("GEMINI_MODEL") or llm_config.get("gemini_model"),
            timeout_seconds=int(_optional_env("GEMINI_TIMEOUT_SECONDS") or llm_config.get("gemini_timeout_seconds") or 90),
            max_output_tokens=int(_optional_env("GEMINI_MAX_OUTPUT_TOKENS") or llm_config.get("gemini_max_output_tokens") or 4096),
        )

    def _client_for(self, provider: str) -> LLMClient | None:
        if provider in self._client_cache:
            return self._client_cache[provider]
        client: LLMClient | None
        if provider == "minimax":
            client = self._build_minimax_client()
            if not client.available():
                LOGGER.info("Skipping minimax provider because MINIMAX_API_KEY is not set")
                client = None
        elif provider == "gemini":
            client = self._build_gemini_client()
            if not client.available():
                reason = "GEMINI_API_KEY is not set" if not client.api_key else "google-genai is not installed"
                LOGGER.info("Skipping gemini provider because %s", reason)
                client = None
        elif provider == "openai":
            LOGGER.info("Skipping openai provider because it is not implemented in this build")
            client = None
        else:
            LOGGER.info("Skipping unsupported LLM provider: %s", provider)
            client = None
        self._client_cache[provider] = client
        return client

    def _client_model_name(self, client: LLMClient) -> str:
        return str(
            client.last_used_model
            or getattr(client, "model", None)
            or getattr(client, "model_name", None)
            or "",
        )

    def _call_with_fallback(self, method_name: str, prompt: str, purpose: str) -> Any:
        attempted: list[dict[str, str]] = []
        errors: list[str] = []
        self.last_attempted_providers = []
        for provider in self._ordered_providers():
            client = self._client_for(provider)
            attempt = {"provider": provider, "model": ""}
            if client is None:
                attempt["error"] = "unavailable"
                attempted.append(attempt)
                self.last_attempted_providers = list(attempted)
                continue
            attempt["model"] = self._client_model_name(client)
            attempted.append(attempt)
            self.last_attempted_providers = list(attempted)
            try:
                result = getattr(client, method_name)(prompt, purpose=purpose)
            except Exception as exc:  # noqa: BLE001
                attempt["error"] = str(exc)
                errors.append(f"{provider}: {exc}")
                LOGGER.info("llm provider failed provider=%s purpose=%s error=%s", provider, purpose, exc)
                continue
            self.last_used_provider = client.last_used_provider or getattr(client, "provider_name", None) or provider
            self.last_used_model = client.last_used_model or self._client_model_name(client)
            return result
        attempted_label = ", ".join(
            f"{item.get('provider')}{'/' + item['model'] if item.get('model') else ''}" for item in attempted
        ) or "none"
        raise RuntimeError(f"No usable LLM provider found. Attempted: {attempted_label}. Errors: {'; '.join(errors) or 'none'}")

    def generate_text(self, prompt: str, purpose: str = "general") -> str:
        result = self._call_with_fallback("generate_text", prompt, purpose)
        return str(result).strip()

    def generate_json(self, prompt: str, purpose: str = "extraction") -> dict[str, Any]:
        result = self._call_with_fallback("generate_json", prompt, purpose)
        if isinstance(result, dict):
            return result
        raise RuntimeError("LLM provider returned a non-dict JSON payload")
