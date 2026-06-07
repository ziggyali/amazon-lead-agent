from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from amazon_lead_agent.llm.router import LLMRouter


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a configured LLM provider.")
    parser.add_argument("--provider", required=True, choices=["minimax", "gemini"])
    parser.add_argument("--fallback-providers", default="", help="Optional comma-separated fallback providers.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env", override=False)
    load_dotenv(override=False)

    router = LLMRouter(provider=args.provider, fallback_providers=args.fallback_providers)
    try:
        reply = router.generate_text("Reply with exactly OK.")
    except Exception as exc:  # noqa: BLE001
        print(f"provider={args.provider}")
        print(f"error={exc}")
        return 1

    print(f"provider={router.last_used_provider}")
    print(f"model={router.last_used_model}")
    print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
