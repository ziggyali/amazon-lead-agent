from __future__ import annotations

import argparse
from pathlib import Path

from amazon_lead_agent.config import load_config, get_storage_path
from amazon_lead_agent.tools.sqlite_store import init_db
from amazon_lead_agent.runtime import run_campaign


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Amazon lead campaign.")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML.")
    parser.add_argument(
        "--mode",
        choices=("full", "discover", "enrich", "score", "draft"),
        default="full",
        help="Pipeline stage to run.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run without creating Gmail drafts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    db_path = get_storage_path(config)
    init_db(db_path)
    if args.dry_run:
        print("DRY RUN enabled: Gmail drafts will not be created.")
    run_campaign(config, db_path, mode=args.mode, dry_run=args.dry_run)
    return 0

