from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from amazon_lead_agent.llm.base import LLMClient, content_to_text, extract_json_object

try:
    from google import genai
    from google.genai import types
except Exception as exc:  # noqa: BLE001
    genai = None
    types = None
    GEMINI_IMPORT_ERROR = exc
else:
    GEMINI_IMPORT_ERROR = None


class GeminiError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass
class GeminiClient(LLMClient):
    api_key: str | None = None
    model: str | None = None
    timeout_seconds: int | None = None
    max_output_tokens: int | None = None
    last_used_provider: str | None = None
    last_used_model: str | None = None

    provider_name: str = "gemini"

    def __post_init__(self) -> None:
        self.api_key = self.api_key or _env("GEMINI_API_KEY")
        self.model = self.model or _env("GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout_seconds = self.timeout_seconds or _int_env("GEMINI_TIMEOUT_SECONDS", 90)
        self.max_output_tokens = self.max_output_tokens or _int_env("GEMINI_MAX_OUTPUT_TOKENS", 4096)

    def available(self) -> bool:
        return bool(self.api_key) and genai is not None

    def _client(self) -> Any:
        if not self.api_key:
            raise GeminiError("GEMINI_API_KEY is not set")
        if genai is None:
            raise GeminiError(f"google-genai is not installed or failed to import: {GEMINI_IMPORT_ERROR}")
        http_options = None
        if types is not None:
            http_options = types.HttpOptions(timeout=self.timeout_seconds * 1000)
        return genai.Client(api_key=self.api_key, http_options=http_options)

    def _generate(self, prompt: str, *, purpose: str, want_json: bool) -> Any:
        client = self._client()
        config: dict[str, Any] = {
            "temperature": 0,
            "max_output_tokens": self.max_output_tokens,
        }
        if want_json:
            config["response_mime_type"] = "application/json"
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        self.last_used_provider = self.provider_name
        self.last_used_model = self.model
        text = getattr(response, "text", "") or ""
        parsed = getattr(response, "parsed", None)
        if want_json:
            if isinstance(parsed, dict):
                return parsed
            if hasattr(parsed, "model_dump"):
                dumped = parsed.model_dump()  # type: ignore[no-any-return]
                if isinstance(dumped, dict):
                    return dumped
            if not text and parsed is not None:
                text = content_to_text(parsed)
            return extract_json_object(text)
        if text:
            return text.strip()
        return content_to_text(parsed).strip()

    def generate_text(self, prompt: str, purpose: str = "general") -> str:
        result = self._generate(prompt, purpose=purpose, want_json=False)
        if not isinstance(result, str):
            return content_to_text(result).strip()
        return result.strip()

    def generate_json(self, prompt: str, purpose: str = "extraction") -> dict[str, Any]:
        try:
            result = self._generate(prompt, purpose=purpose, want_json=True)
        except ValueError as exc:
            raise GeminiError(str(exc)) from exc
        if isinstance(result, dict):
            return result
        try:
            return extract_json_object(content_to_text(result))
        except ValueError as exc:
            raise GeminiError(str(exc)) from exc
