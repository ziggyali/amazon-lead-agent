import os
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

    @patch("amazon_lead_agent.runtime.get_storage_router")
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
        mock_get_storage_router,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)

            class FakeStorage:
                uses_sheets = True

                def __init__(self):
                    self.upserts = []
                    self.outreach = []
                    self.reports = []
                    self.sheet_mirror_error_count = 0
                    self.failed_sheet_rows = []

                def upsert_lead(self, lead, tab=None):
                    self.upserts.append((tab, lead))
                    return lead.get("id", "lead-1")

                def record_outreach_event(self, event):
                    self.outreach.append(event)

                def get_leads_for_enrichment(self, limit):
                    return []

                def get_leads_for_scoring(self, limit):
                    return []

                def get_leads_for_drafting(self, min_score, limit):
                    return []

                def append_daily_report(self, report):
                    self.reports.append(report)

                def snapshot(self):
                    return {
                        "sheet_mirror_error_count": self.sheet_mirror_error_count,
                        "failed_sheet_rows": self.failed_sheet_rows,
                        "uses_sheets": True,
                    }

                def commit(self):
                    return None

                def close(self):
                    return None

            fake_storage = FakeStorage()
            mock_get_storage_router.return_value = fake_storage
            def discover_side_effect(config, storage):
                lead = {
                    "id": "lead-1",
                    "company_name": "Acme",
                    "website": "https://example.com",
                    "status": "discovered",
                }
                storage.upsert_lead(lead, tab="Lead Queue")
                storage.record_outreach_event({"lead_id": "lead-1", "event_type": "discovered", "metadata": {"status": "discovered"}})
                return {
                    "leads": [lead],
                    "search_stats": {
                        "provider_counts": {"duckduckgo": 1},
                        "blocked_query_counts": {"duckduckgo": 0},
                        "rate_limited_query_counts": {"duckduckgo": 0},
                        "rejected_content_domains_count": 0,
                        "rejected_listicle_domains_count": 0,
                        "hard_rejected_junk_count": 0,
                        "soft_pass_needs_enrichment_count": 1,
                        "rejected_due_to_no_amazon_evidence_count": 0,
                        "discovered_count_by_category": {"beauty": 1},
                    },
                }

            def extraction_side_effect(config, storage):
                lead = {
                    "id": "lead-1",
                    "company_name": "Acme",
                    "website": "https://example.com",
                    "status": "enriched",
                    "extraction_method": "gemini_direct",
                    "llm_provider_used": "gemini",
                    "llm_model_used": "gemini-2.5-flash",
                }
                storage.upsert_lead(lead, tab="Lead Queue")
                storage.record_outreach_event({"lead_id": "lead-1", "event_type": "enriched", "metadata": lead})
                return [lead]

            def scoring_side_effect(config, storage):
                approved = {
                    "id": "lead-1",
                    "company_name": "Acme",
                    "website": "https://example.com",
                    "status": "approved",
                    "score": 90,
                    "tier": "A",
                    "public_emails": ["hello@example.com"],
                    "contact_page_url": "https://example.com/contact",
                    "extraction_method": "gemini_direct",
                    "send_status": "pending",
                }
                rejected = {
                    "id": "lead-2",
                    "company_name": "Brand Two",
                    "website": "https://brandtwo.com",
                    "status": "rejected",
                    "score": 20,
                    "tier": "Reject",
                    "extraction_method": "blocked_or_error",
                }
                contact_queue = {
                    "id": "lead-3",
                    "company_name": "Brand Three",
                    "website": "https://brandthree.com",
                    "status": "contact_form_queue",
                    "score": 80,
                    "tier": "B",
                    "contact_page_url": "https://brandthree.com/contact",
                    "public_emails": [],
                    "extraction_method": "minimax_direct_m3",
                }
                storage.upsert_lead(approved, tab="Approved Leads")
                storage.upsert_lead(rejected, tab="Rejected Leads")
                storage.upsert_lead(contact_queue, tab="Contact Form Queue")
                return [approved, rejected, contact_queue]

            mock_discovery.side_effect = discover_side_effect
            mock_extraction.side_effect = extraction_side_effect
            mock_scoring.side_effect = scoring_side_effect
            config = {
                "storage": {"google_sheet_id": "sheet-123"},
                "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10, "daily_discovery_limit": 10},
                "sender": {"name": "Zaigham Ali", "offer": "Offer"},
                "llm": {"provider": "gemini"},
            }
            report = run_campaign(config, db_path, mode="full", dry_run=True)

            self.assertEqual(report["drafts_created"], 0)
            self.assertGreater(len(fake_storage.upserts), 0)
            self.assertGreater(len(fake_storage.reports), 0)
            tabs = [tab for tab, _ in fake_storage.upserts]
            self.assertIn("Lead Queue", tabs)
            self.assertIn("Approved Leads", tabs)
            self.assertIn("Contact Form Queue", tabs)
            self.assertIn("Rejected Leads", tabs)
            mock_outreach.assert_called_once()
            self.assertFalse(any(event.get("event_type") == "draft_created" for event in fake_storage.outreach))
            self.assertEqual(report["search_provider_counts"], {"duckduckgo": 1})
            self.assertEqual(report["provider_blocked_counts"], {"duckduckgo": 0})
            self.assertEqual(report["queries_attempted_by_provider"], {"duckduckgo": 1})
            self.assertEqual(report["cleaned_redirect_count"], 0)
            self.assertEqual(report["rejected_redirect_count"], 0)
            self.assertEqual(report["soft_pass_needs_enrichment_count"], 1)
            self.assertEqual(report["discovered_count_by_category"], {"beauty": 1})

    @patch("amazon_lead_agent.runtime.get_storage_router")
    @patch("amazon_lead_agent.runtime.run_outreach", return_value=[])
    @patch("amazon_lead_agent.runtime.run_scoring", return_value=[])
    @patch("amazon_lead_agent.runtime.run_extraction", return_value=[])
    @patch("amazon_lead_agent.runtime.run_discovery")
    def test_sheet_write_error_does_not_crash_and_report_is_written(
        self,
        mock_discovery,
        mock_extraction,
        mock_scoring,
        mock_outreach,
        mock_get_storage_router,
    ) -> None:
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
                "provider_counts": {"bing_html": 1},
                "provider_blocked_counts": {"bing_html": 0},
                "queries_attempted_by_provider": {"bing_html": 1},
                "blocked_query_counts": {"bing_html": 0},
                "rate_limited_query_counts": {"bing_html": 0},
                "rejected_content_domain_count": 0,
                "rejected_listicle_domains_count": 0,
                "cleaned_redirect_count": 1,
                "rejected_redirect_count": 0,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)

            class FailingStorage:
                uses_sheets = True

                def __init__(self):
                    self.failed_sheet_rows = [{"tab": "Lead Queue", "error": "Invalid values[1][16]"}]
                    self.sheet_mirror_error_count = 1

                def upsert_lead(self, lead, tab=None):
                    raise ValueError("Invalid values[1][16]")

                def record_outreach_event(self, event):
                    raise ValueError("Invalid values[1][16]")

                def get_leads_for_enrichment(self, limit):
                    return []

                def get_leads_for_scoring(self, limit):
                    return []

                def get_leads_for_drafting(self, min_score, limit):
                    return []

                def append_daily_report(self, report):
                    raise ValueError("Invalid values[1][16]")

                def snapshot(self):
                    return {
                        "sheet_mirror_error_count": self.sheet_mirror_error_count,
                        "failed_sheet_rows": self.failed_sheet_rows,
                        "uses_sheets": True,
                    }

                def commit(self):
                    return None

                def close(self):
                    return None

            mock_get_storage_router.return_value = FailingStorage()
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                report = run_campaign(
                    {
                        "storage": {"google_sheet_id": "sheet-123"},
                        "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10, "daily_discovery_limit": 10},
                        "sender": {"name": "Zaigham Ali", "offer": "Offer"},
                    },
                    db_path,
                    mode="full",
                    dry_run=True,
                )
            finally:
                os.chdir(old_cwd)
            self.assertGreaterEqual(report["sheet_mirror_error_count"], 1)
            self.assertTrue(report["failed_sheet_rows"])
            self.assertTrue(Path(tmpdir, "campaign_report.md").exists())
            self.assertTrue(report["campaign_report_path"])


if __name__ == "__main__":
    unittest.main()
