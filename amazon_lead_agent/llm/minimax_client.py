from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any

import requests


class MiniMaxError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_blocks: list[str] = []
        fallback_blocks: list[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = str(block.get("type", "")).lower()
                text = block.get("text") or block.get("content") or ""
                if block_type == "text" and text:
                    text_blocks.append(str(text))
                elif text:
                    fallback_blocks.append(str(text))
            elif isinstance(block, str):
                fallback_blocks.append(block)
        if text_blocks:
            return "\n".join(text_blocks)
        if fallback_blocks:
            return "\n".join(fallback_blocks)
        return ""
    if isinstance(content, dict):
        if "text" in content:
            return str(content["text"])
        if "content" in content:
            return _content_to_text(content["content"])
    return str(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise MiniMaxError("MiniMax response was empty")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        loaded = json.loads(stripped)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match:
        loaded = json.loads(match.group(0))
        if isinstance(loaded, dict):
            return loaded
    raise MiniMaxError("MiniMax response did not contain valid JSON")


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
        if api_style == "chatcompletion_v2":
            if isinstance(data, dict):
                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                return _content_to_text(content)
        elif api_style == "anthropic_messages":
            if isinstance(data, dict):
                content = data.get("content")
                return _content_to_text(content)
        return _content_to_text(data)

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
        return _extract_json_object(raw)


def get_default_client() -> MiniMaxClient:
    return MiniMaxClient()
