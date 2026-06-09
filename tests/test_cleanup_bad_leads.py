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

    def test_www_blocked_domain_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            conn = get_connection(db_path)
            try:
                upsert_lead(
                    conn,
                    {
                        "id": "lead-2",
                        "company_name": "Blocked Domain",
                        "website": "https://www.popsugar.com/article",
                        "score": 10,
                        "tier": "Reject",
                        "status": "approved",
                        "review_status": "approved",
                        "send_status": "pending",
                        "amazon_backlink_found": False,
                        "public_emails": [],
                        "contact_page_url": "",
                        "extraction_method": "scrapegraphai_other",
                    },
                )
                conn.commit()
                actions = find_cleanup_actions(conn)
                self.assertEqual(len(actions), 1)
                self.assertEqual(actions[0]["id"], "lead-2")
                self.assertEqual(actions[0]["status"], "rejected")
                self.assertEqual(actions[0]["reason"], "blocked domain")
            finally:
                conn.close()

    def test_available_definition_name_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            conn = get_connection(db_path)
            try:
                upsert_lead(
                    conn,
                    {
                        "id": "lead-3",
                        "company_name": "AVAILABLE Definition & Meaning",
                        "website": "https://brand.example.com",
                        "score": 5,
                        "tier": "Reject",
                        "status": "approved",
                        "review_status": "approved",
                        "send_status": "pending",
                        "amazon_backlink_found": False,
                        "public_emails": [],
                        "contact_page_url": "",
                        "extraction_method": "scrapegraphai_other",
                    },
                )
                conn.commit()
                actions = find_cleanup_actions(conn)
                self.assertEqual(len(actions), 1)
                self.assertEqual(actions[0]["status"], "rejected")
                self.assertEqual(actions[0]["reason"], "available-like company name")
            finally:
                conn.close()

    def test_contact_form_queue_without_amazon_is_demoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            conn = get_connection(db_path)
            try:
                upsert_lead(
                    conn,
                    {
                        "id": "lead-4",
                        "company_name": "Brand Example",
                        "website": "https://brand.example.com",
                        "category": "beauty",
                        "score": 80,
                        "tier": "B",
                        "status": "contact_form_queue",
                        "review_status": "needs_contact_form",
                        "send_status": "contact_form_queue",
                        "public_emails": [],
                        "contact_page_url": "https://brand.example.com/contact",
                        "amazon_backlink_found": False,
                        "amazon_links": [],
                        "extraction_method": "minimax_direct_m3",
                    },
                )
                conn.commit()
                actions = find_cleanup_actions(conn)
                self.assertEqual(len(actions), 1)
                self.assertEqual(actions[0]["status"], "needs_enrichment")
                self.assertEqual(actions[0]["reason"], "positive queue without verified Amazon evidence")
                updated = apply_cleanup(conn, actions)
                self.assertEqual(updated, 1)
                row = conn.execute("SELECT status, review_status, send_status, tier, score, cleanup_reason FROM leads WHERE id = ?", ("lead-4",)).fetchone()
                self.assertEqual(row["status"], "needs_enrichment")
                self.assertEqual(row["review_status"], "needs_enrichment")
                self.assertEqual(row["send_status"], "not_eligible")
                self.assertEqual(row["tier"], "C")
                self.assertLessEqual(row["score"], 45)
                self.assertEqual(row["cleanup_reason"], "positive queue without verified Amazon evidence")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
