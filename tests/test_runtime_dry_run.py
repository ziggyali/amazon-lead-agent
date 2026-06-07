import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amazon_lead_agent.agents.outreach_agent import run_outreach
from amazon_lead_agent.tools.sqlite_store import get_connection, init_db, upsert_lead


class DryRunTests(unittest.TestCase):
    @patch("amazon_lead_agent.agents.outreach_agent.create_gmail_draft")
    def test_dry_run_does_not_create_gmail_drafts(self, mock_create_draft) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            conn = get_connection(db_path)
            try:
                upsert_lead(
                    conn,
                    {
                        "id": "lead-1",
                        "company_name": "Acme",
                        "website": "https://example.com",
                        "category": "beauty",
                        "amazon_backlink_found": True,
                        "public_emails": ["hello@example.com"],
                        "contact_page_url": "https://example.com/contact",
                        "score": 90,
                        "tier": "A",
                        "status": "approved",
                        "send_status": "pending",
                        "source_urls": ["https://example.com"],
                    },
                )
                conn.commit()
            finally:
                conn.close()

            config = {
                "storage": {"google_sheet_id": ""},
                "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10},
                "sender": {"name": "Zaigham Ali", "offer": "Offer"},
            }
            results = run_outreach(config, db_path, dry_run=True)
            self.assertEqual(mock_create_draft.call_count, 0)
            self.assertEqual(len(results), 1)
            conn = get_connection(db_path)
            try:
                row = conn.execute("SELECT send_status, draft_preview_subject FROM leads WHERE id = ?", ("lead-1",)).fetchone()
                self.assertEqual(row["send_status"], "draft_preview")
                self.assertTrue(row["draft_preview_subject"])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()

