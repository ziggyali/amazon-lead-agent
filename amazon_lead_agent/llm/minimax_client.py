from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import requests

from amazon_lead_agent.llm.base import content_to_text, extract_json_object


class MiniMaxError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass
class MiniMaxClient:
    api_key: str | None = None
    api_style: str | None = None
    model: str | None = None
    api_base: str | None = None
    fallback_model: str | None = None
    fallback_api_style: str | None = None
    fallback_api_base: str | None = None
    timeout_seconds: int | None = None
    max_tokens_discovery: int | None = None
    max_tokens_research: int | None = None
    last_used_model: str | None = None
    last_used_style: str | None = None
    last_used_provider: str | None = None
    provider_name: str = "minimax"

    def __post_init__(self) -> None:
        self.api_key = self.api_key or _env("MINIMAX_API_KEY")
        self.api_style = self.api_style or _env("MINIMAX_API_STYLE", "chatcompletion_v2")
        self.model = self.model or _env("MINIMAX_MODEL", "MiniMax-M3")
        self.api_base = self.api_base or _env("MINIMAX_API_BASE", "https://api.minimax.io/v1/text/chatcompletion_v2")
        self.fallback_model = self.fallback_model or _env("MINIMAX_FALLBACK_MODEL", "MiniMax-M2.7")
        self.fallback_api_style = self.fallback_api_style or _env("MINIMAX_FALLBACK_API_STYLE", "anthropic_messages")
        self.fallback_api_base = self.fallback_api_base or _env("MINIMAX_FALLBACK_API_BASE", "https://api.minimax.io/anthropic/v1/messages")
        self.timeout_seconds = self.timeout_seconds or _int_env("MINIMAX_TIMEOUT_SECONDS", 90)
        self.max_tokens_discovery = self.max_tokens_discovery or _int_env("MINIMAX_MAX_TOKENS_DISCOVERY", 3000)
        self.max_tokens_research = self.max_tokens_research or _int_env("MINIMAX_MAX_TOKENS_RESEARCH", 2048)

    def _headers(self, style: str) -> dict[str, str]:
        if not self.api_key:
            raise MiniMaxError("MINIMAX_API_KEY is not set")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if style == "anthropic_messages":
            headers["anthropic-version"] = "2023-06-01"
            headers["anthropic-dangerous-direct-access-only"] = "true"
        return headers

    def _request(self, *, prompt: str, model: str, api_style: str, api_base: str, max_tokens: int, purpose: str) -> str:
        if api_style == "chatcompletion_v2":
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            }
        elif api_style == "anthropic_messages":
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            }
        else:
            raise MiniMaxError(f"Unsupported MiniMax API style: {api_style}")

        response = requests.post(
            api_base,
            headers=self._headers(api_style),
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        self.last_used_model = model
        self.last_used_style = api_style
        self.last_used_provider = self.provider_name
        if api_style == "chatcompletion_v2":
            if isinstance(data, dict):
                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                return content_to_text(content)
        elif api_style == "anthropic_messages":
            if isinstance(data, dict):
                content = data.get("content")
                return content_to_text(content)
        return content_to_text(data)

    def _call_with_fallback(self, prompt: str, purpose: str, max_tokens: int) -> str:
        primary_error: Exception | None = None
        try:
            return self._request(
                prompt=prompt,
                model=self.model,
                api_style=self.api_style,
                api_base=self.api_base,
                max_tokens=max_tokens,
                purpose=purpose,
            )
        except Exception as exc:  # noqa: BLE001
            primary_error = exc
        if self.fallback_model and self.fallback_api_style and self.fallback_api_base:
            try:
                return self._request(
                    prompt=prompt,
                    model=self.fallback_model,
                    api_style=self.fallback_api_style,
                    api_base=self.fallback_api_base,
                    max_tokens=max_tokens,
                    purpose=purpose,
                )
            except Exception as fallback_exc:  # noqa: BLE001
                raise MiniMaxError(f"MiniMax primary and fallback requests failed: {primary_error}; {fallback_exc}") from fallback_exc
        raise MiniMaxError(f"MiniMax request failed: {primary_error}") from primary_error

    def generate_text(self, prompt: str, purpose: str = "general") -> str:
        max_tokens = self.max_tokens_discovery if purpose == "discovery" else self.max_tokens_research if purpose == "research" else self.max_tokens_research
        return self._call_with_fallback(prompt, purpose, max_tokens).strip()

    def generate_json(self, prompt: str, purpose: str = "extraction") -> dict[str, Any]:
        raw = self._call_with_fallback(prompt, purpose, self.max_tokens_research)
        try:
            return extract_json_object(raw)
        except ValueError as exc:
            raise MiniMaxError(str(exc)) from exc

    def available(self) -> bool:
        return bool(self.api_key)


def get_default_client() -> MiniMaxClient:
    return MiniMaxClient()
