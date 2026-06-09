import tempfile
import unittest
from pathlib import Path

from amazon_lead_agent.tools.sqlite_store import get_connection, init_db, upsert_lead
from scripts.cleanup_bad_leads import find_cleanup_actions, apply_cleanup


class CleanupBadLeadsTests(unittest.TestCase):
    def test_dry_run_catches_blocked_or_error_approved_lead(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            conn = get_connection(db_path)
            try:
                upsert_lead(
                    conn,
                    {
                        "id": "lead-1",
                        "company_name": "Old Lead",
                        "website": "https://example.com",
                        "score": 90,
                        "tier": "A",
                        "status": "approved",
                        "review_status": "approved",
                        "send_status": "pending",
                        "amazon_backlink_found": False,
                        "public_emails": [],
                        "contact_page_url": "",
                        "extraction_method": "blocked_or_error",
                    },
                )
                conn.commit()
                actions = find_cleanup_actions(conn)
                self.assertEqual(len(actions), 1)
                self.assertEqual(actions[0]["id"], "lead-1")
                self.assertEqual(actions[0]["current_status"], "approved")
                self.assertEqual(actions[0]["status"], "needs_enrichment")
                self.assertEqual(actions[0]["reason"], "blocked_or_error high score without verified signals")
                updated = apply_cleanup(conn, actions)
                self.assertEqual(updated, 1)
                row = conn.execute("SELECT status, review_status, send_status FROM leads WHERE id = ?", ("lead-1",)).fetchone()
                self.assertEqual(row["status"], "needs_enrichment")
                self.assertEqual(row["review_status"], "needs_enrichment")
                self.assertEqual(row["send_status"], "not_eligible")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
