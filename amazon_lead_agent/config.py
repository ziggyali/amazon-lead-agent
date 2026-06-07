from __future__ import annotations

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
        "sqlite_path": "data/leads.db",
        "google_sheet_id": "",
    },
    "llm": {"provider": "minimax"},
    "gmail": {"drafts_only": True},
}


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return merge_config(DEFAULT_CONFIG, loaded)


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

