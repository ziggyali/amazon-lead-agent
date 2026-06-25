from __future__ import annotations

import argparse

from amazon_lead_agent.config import get_storage_path, load_config
from amazon_lead_agent.tools.lead_queue_migration import migrate_lead_queue_rows
from amazon_lead_agent.tools.storage_router import get_storage_router


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair Lead Queue identity fields for seeded brands.")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML.")
    parser.add_argument("--apply", action="store_true", help="Write repaired rows back to storage.")
    parser.add_argument("--dry-run", action="store_true", help="Preview repairs without writing.")
    parser.add_argument("--delete-junk", action="store_true", help="Delete junk rows when the storage backend supports it.")
    args = parser.parse_args()

    dry_run = True if args.dry_run or not args.apply else False
    config = load_config(args.config)
    db_path = get_storage_path(config)
    storage = get_storage_router(config, db_path)
    try:
        summary = migrate_lead_queue_rows(storage, dry_run=dry_run, delete_junk=bool(args.delete_junk))
        mode = "dry-run" if dry_run else "apply"
        print(f"mode={mode}")
        print(f"rows_seen={summary.rows_seen}")
        print(f"rows_changed={summary.rows_changed}")
        print(f"rows_skipped={summary.rows_skipped}")
        print(f"junk_rows={summary.junk_rows}")
        print(f"duplicate_rows={summary.duplicate_rows}")
        for row in summary.repaired_rows[:20]:
            print(
                "repaired lead_id={lead_id} brand_name={brand_name} website={website} status={status}".format(
                    **row,
                ),
            )
        return 0
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
