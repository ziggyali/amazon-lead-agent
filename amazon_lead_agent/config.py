from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG = {
    "campaign": {
        "categories": ["beauty", "pet", "home", "supplements"],
        "daily_discovery_limit": 50,
        "daily_draft_limit": 10,
        "minimum_score_for_draft": 75,
        "minimum_score_for_auto_send": 90,
    },
    "sender": {
        "name": "Zaigham Ali",
        "website": "https://zaighamali.com",
        "linkedin": "https://linkedin.com/zaighamali-",
        "offer": "We take the messy, time-consuming operational work off your plate and keep your Amazon account clean, compliant, and conversion-ready.",
    },
    "storage": {
        "storage_mode": "sheets",
        "local_cache_enabled": False,
        "sqlite_path": "data/leads.db",
        "google_sheet_id": "",
    },
    "llm": {
        "provider": "minimax",
        "allow_heuristic_fallback": True,
        "enable_scrapegraphai": False,
        "minimax_api_style": "chatcompletion_v2",
        "minimax_model": "MiniMax-M3",
        "minimax_api_base": "https://api.minimax.io/v1/text/chatcompletion_v2",
        "minimax_fallback_model": "MiniMax-M2.7",
        "minimax_fallback_api_style": "anthropic_messages",
        "minimax_fallback_api_base": "https://api.minimax.io/anthropic/v1/messages",
        "minimax_timeout_seconds": 90,
        "minimax_max_tokens_discovery": 3000,
        "minimax_max_tokens_research": 2048,
    },
    "gmail": {"drafts_only": True},
}


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    config = merge_config(DEFAULT_CONFIG, loaded)
    return apply_env_overrides(config)


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in base.items():
        if isinstance(value, dict):
            result[key] = merge_config(value, override.get(key, {}))
        else:
            result[key] = override.get(key, value)
    for key, value in override.items():
        if key not in result:
            result[key] = value
    return result


def get_storage_path(config: dict[str, Any]) -> Path:
    return Path(config["storage"]["sqlite_path"])


def _coerce_env_value(value: str, sample: Any) -> Any:
    if isinstance(sample, bool):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(sample, int) and not isinstance(sample, bool):
        try:
            return int(value)
        except ValueError:
            return sample
    return value


def _set_path(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target = config
    for key in path[:-1]:
        target = target.setdefault(key, {})
    target[path[-1]] = value


def apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    env_map = {
        "STORAGE_MODE": ("storage", "storage_mode"),
        "LOCAL_CACHE_ENABLED": ("storage", "local_cache_enabled"),
        "GOOGLE_SHEET_ID": ("storage", "google_sheet_id"),
        "DAILY_DISCOVERY_LIMIT": ("campaign", "daily_discovery_limit"),
        "DAILY_DRAFT_LIMIT": ("campaign", "daily_draft_limit"),
        "MINIMUM_SCORE_FOR_DRAFT": ("campaign", "minimum_score_for_draft"),
        "MINIMUM_SCORE_FOR_AUTO_SEND": ("campaign", "minimum_score_for_auto_send"),
        "ALLOW_HEURISTIC_FALLBACK": ("llm", "allow_heuristic_fallback"),
        "ENABLE_SCRAPEGRAPHAI": ("llm", "enable_scrapegraphai"),
        "MINIMAX_API_STYLE": ("llm", "minimax_api_style"),
        "MINIMAX_MODEL": ("llm", "minimax_model"),
        "MINIMAX_API_BASE": ("llm", "minimax_api_base"),
        "MINIMAX_FALLBACK_MODEL": ("llm", "minimax_fallback_model"),
        "MINIMAX_FALLBACK_API_STYLE": ("llm", "minimax_fallback_api_style"),
        "MINIMAX_FALLBACK_API_BASE": ("llm", "minimax_fallback_api_base"),
        "MINIMAX_TIMEOUT_SECONDS": ("llm", "minimax_timeout_seconds"),
        "MINIMAX_MAX_TOKENS_DISCOVERY": ("llm", "minimax_max_tokens_discovery"),
        "MINIMAX_MAX_TOKENS_RESEARCH": ("llm", "minimax_max_tokens_research"),
    }
    for env_name, path in env_map.items():
        if env_name not in os.environ:
            continue
        raw_value = os.environ[env_name]
        if raw_value == "":
            continue
        current = config
        for key in path[:-1]:
            current = current[key]
        sample = current[path[-1]]
        _set_path(config, path, _coerce_env_value(raw_value, sample))
    return config

