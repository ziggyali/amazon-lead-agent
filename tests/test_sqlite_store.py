import sqlite3
import tempfile
import unittest
from pathlib import Path

from amazon_lead_agent.tools.sqlite_store import (
    get_connection,
    get_leads_for_drafting,
    init_db,
    mark_draft_created,
    record_outreach_event,
    upsert_lead,
)


class SQLiteStoreTests(unittest.TestCase):
    def test_init_upsert_and_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            conn = get_connection(db_path)
            try:
                lead_id = upsert_lead(
                    conn,
                    {
                        "company_name": "Acme Brands LLC",
                        "website": "https://example.com",
                        "category": "beauty",
                        "amazon_backlink_found": True,
                        "public_emails": ["hello@example.com"],
                        "contact_page_url": "https://example.com/contact",
                        "score": 80,
                        "tier": "B",
                        "status": "scored",
                        "source_urls": ["https://example.com"],
                    },
                )
                conn.commit()
                rows = get_leads_for_drafting(conn, min_score=75, limit=10)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["id"], lead_id)
                mark_draft_created(conn, lead_id, "draft-123")
                conn.commit()
                refreshed = conn.execute("SELECT drafted, draft_id, status FROM leads WHERE id = ?", (lead_id,)).fetchone()
                self.assertEqual(refreshed["drafted"], 1)
                self.assertEqual(refreshed["draft_id"], "draft-123")
                self.assertEqual(refreshed["status"], "drafted")
            finally:
                conn.close()

    def test_blocked_or_error_is_not_draft_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            conn = get_connection(db_path)
            try:
                upsert_lead(
                    conn,
                    {
                        "id": "lead-2",
                        "company_name": "Acme",
                        "website": "https://example.com",
                        "category": "beauty",
                        "amazon_backlink_found": True,
                        "public_emails": ["hello@example.com"],
                        "contact_page_url": "https://example.com/contact",
                        "score": 95,
                        "tier": "A",
                        "status": "approved",
                        "extraction_method": "blocked_or_error",
                        "source_urls": ["https://example.com"],
                    },
                )
                conn.commit()
                rows = get_leads_for_drafting(conn, min_score=75, limit=10)
                self.assertEqual(rows, [])
            finally:
                conn.close()

    def test_record_outreach_event_updates_daily_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            conn = get_connection(db_path)
            try:
                lead_id = upsert_lead(conn, {"company_name": "Acme", "website": "https://example.com"})
                record_outreach_event(conn, {"lead_id": lead_id, "event_type": "draft_created", "metadata": {"x": 1}})
                conn.commit()
                report = conn.execute("SELECT draft_count FROM daily_reports").fetchone()
                self.assertEqual(report["draft_count"], 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

