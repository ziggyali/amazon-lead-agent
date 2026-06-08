import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amazon_lead_agent.agents.outreach_agent import run_outreach
from amazon_lead_agent.runtime import run_campaign
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

    @patch("amazon_lead_agent.runtime.append_daily_report")
    @patch("amazon_lead_agent.runtime.append_outreach_log")
    @patch("amazon_lead_agent.runtime.append_or_update_lead")
    @patch("amazon_lead_agent.runtime.run_outreach", return_value=[])
    @patch("amazon_lead_agent.runtime.run_scoring")
    @patch("amazon_lead_agent.runtime.run_extraction")
    @patch("amazon_lead_agent.runtime.run_discovery")
    def test_dry_run_mirrors_to_sheets_but_creates_no_drafts(
        self,
        mock_discovery,
        mock_extraction,
        mock_scoring,
        mock_outreach,
        mock_append_lead,
        mock_append_outreach,
        mock_append_report,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            mock_discovery.return_value = {
                "leads": [
                    {
                        "id": "lead-1",
                        "company_name": "Acme",
                        "website": "https://example.com",
                        "status": "discovered",
                    }
                ],
                "search_stats": {
                    "provider_counts": {"duckduckgo": 1},
                    "blocked_query_counts": {"duckduckgo": 0},
                    "rate_limited_query_counts": {"duckduckgo": 0},
                    "rejected_content_domains_count": 0,
                    "rejected_listicle_domains_count": 0,
                },
            }
            mock_extraction.return_value = [
                {
                    "id": "lead-1",
                    "company_name": "Acme",
                    "website": "https://example.com",
                    "status": "enriched",
                    "extraction_method": "gemini_direct",
                    "llm_provider_used": "gemini",
                    "llm_model_used": "gemini-2.5-flash",
                }
            ]
            mock_scoring.return_value = [
                {
                    "id": "lead-1",
                    "company_name": "Acme",
                    "website": "https://example.com",
                    "status": "approved",
                    "score": 90,
                    "tier": "A",
                    "public_emails": ["hello@example.com"],
                    "contact_page_url": "https://example.com/contact",
                    "extraction_method": "gemini_direct",
                },
                {
                    "id": "lead-2",
                    "company_name": "Brand Two",
                    "website": "https://brandtwo.com",
                    "status": "rejected",
                    "score": 20,
                    "tier": "Reject",
                    "extraction_method": "blocked_or_error",
                },
                {
                    "id": "lead-3",
                    "company_name": "Brand Three",
                    "website": "https://brandthree.com",
                    "status": "contact_form_queue",
                    "score": 80,
                    "tier": "B",
                    "contact_page_url": "https://brandthree.com/contact",
                    "public_emails": [],
                    "extraction_method": "minimax_direct_m3",
                },
            ]
            config = {
                "storage": {"google_sheet_id": "sheet-123"},
                "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10, "daily_discovery_limit": 10},
                "sender": {"name": "Zaigham Ali", "offer": "Offer"},
                "llm": {"provider": "gemini"},
            }
            report = run_campaign(config, db_path, mode="full", dry_run=True)

            self.assertEqual(report["drafts_created"], 0)
            self.assertGreater(mock_append_lead.call_count, 0)
            self.assertTrue(mock_append_report.called)
            tabs = [call.args[1] for call in mock_append_lead.call_args_list]
            self.assertIn("Lead Queue", tabs)
            self.assertIn("Approved Leads", tabs)
            self.assertIn("Contact Form Queue", tabs)
            self.assertIn("Rejected Leads", tabs)
            mock_outreach.assert_called_once()
            self.assertFalse(any("draft_created" in str(call.args) for call in mock_append_outreach.call_args_list))


if __name__ == "__main__":
    unittest.main()
