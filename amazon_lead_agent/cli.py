from __future__ import annotations

import argparse
from pathlib import Path

from amazon_lead_agent.config import load_config, get_storage_path
from amazon_lead_agent.tools.sqlite_store import init_db
from amazon_lead_agent.agents.discovery_agent import run_discovery
from amazon_lead_agent.agents.extraction_agent import run_extraction
from amazon_lead_agent.agents.scoring_agent import run_scoring
from amazon_lead_agent.agents.outreach_agent import run_outreach


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Amazon lead campaign.")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML.")
    parser.add_argument(
        "--mode",
        choices=("full", "discover", "enrich", "score", "draft"),
        default="full",
        help="Pipeline stage to run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    db_path = get_storage_path(config)
    init_db(db_path)

    if args.mode in ("full", "discover"):
        run_discovery(config, db_path)
    if args.mode in ("full", "enrich"):
        run_extraction(config, db_path)
    if args.mode in ("full", "score"):
        run_scoring(config, db_path)
    if args.mode in ("full", "draft"):
        run_outreach(config, db_path)
    return 0

