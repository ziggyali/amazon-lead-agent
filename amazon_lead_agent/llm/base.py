from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    provider_name: str
    model_name: str | None = None
    last_used_provider: str | None = None
    last_used_model: str | None = None

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def generate_text(self, prompt: str, purpose: str = "general") -> str:
        raise NotImplementedError

    @abstractmethod
    def generate_json(self, prompt: str, purpose: str = "extraction") -> dict[str, Any]:
        raise NotImplementedError


def content_to_text(content: Any) -> str:
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
            return content_to_text(content["content"])
    return str(content)


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("response was empty")
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
    raise ValueError("response did not contain valid JSON")
