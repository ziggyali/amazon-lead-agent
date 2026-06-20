import os
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amazon_lead_agent.runtime import run_campaign


class TracerModeTests(unittest.TestCase):
    @patch("amazon_lead_agent.runtime.extract_brand_profile")
    @patch("amazon_lead_agent.runtime.get_storage_router")
    def test_tracer_mode_writes_local_artifacts_and_cites_evidence(self, mock_get_storage_router, mock_extract_brand_profile) -> None:
        class FakeStorage:
            uses_sheets = True

            def __init__(self):
                self.upserts = []
                self.events = []
                self.reports = []
                self.commits = 0

            def get_leads_for_enrichment(self, limit):
                return [
                    {
                        "id": "lead-1",
                        "lead_id": "lead-1",
                        "company_name": "Glossier",
                        "brand_name": "Glossier",
                        "website": "https://www.glossier.com",
                        "category": "beauty",
                        "status": "needs_enrichment",
                        "source_urls": ["https://www.glossier.com"],
                    },
                    {
                        "id": "lead-2",
                        "lead_id": "lead-2",
                        "company_name": "Cocokind",
                        "brand_name": "Cocokind",
                        "website": "https://www.cocokind.com",
                        "category": "beauty",
                        "status": "needs_enrichment",
                        "source_urls": ["https://www.cocokind.com"],
                    },
                ][:limit]

            def upsert_lead(self, lead, tab=None):
                self.upserts.append((tab, dict(lead)))
                return lead.get("id", "lead-1")

            def record_outreach_event(self, event):
                self.events.append(dict(event))

            def append_daily_report(self, report):
                self.reports.append(dict(report))

            def commit(self):
                self.commits += 1

            def close(self):
                return None

        def fake_extract_brand_profile(url, minimax_api_key=None, llm_config=None):
            brand = "Glossier" if "glossier" in url else "Cocokind"
            return {
                "company_name": brand,
                "brand_name": brand,
                "website": url,
                "category": "beauty",
                "amazon_links": ["https://www.amazon.com/stores/brand"],
                "amazon_evidence_url": "https://www.amazon.com/stores/brand",
                "amazon_evidence_urls": ["https://www.amazon.com/stores/brand"],
                "amazon_backlink_found": True,
                "amazon_evidence_summary": "Brand links to an Amazon storefront.",
                "public_emails": ["hello@example.com"],
                "contact_page_url": f"{url}/contact",
                "founder_or_executive_names": ["Founder Example"],
                "decision_maker_title": "Founder",
                "pain_points": ["Amazon operations"],
                "source_quotes": ["Official site"],
                "source_urls": [url],
                "confidence": 0.9,
                "extraction_method": "minimax_direct_m3",
                "llm_provider_used": "minimax",
                "llm_model_used": "MiniMax-M3",
                "llm_attempted_providers": [{"provider": "minimax", "model": "MiniMax-M3"}],
            }

        fake_storage = FakeStorage()
        mock_get_storage_router.return_value = fake_storage
        mock_extract_brand_profile.side_effect = fake_extract_brand_profile

        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                report = run_campaign(
                    {
                        "storage": {"google_sheet_id": "sheet-123"},
                        "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10, "daily_discovery_limit": 8, "categories": ["beauty"]},
                        "sender": {"name": "Zaigham Ali", "offer": "Offer"},
                        "llm": {"provider": "minimax"},
                    },
                    cwd / "leads.db",
                    mode="tracer",
                    dry_run=True,
                )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(report["mode"], "tracer")
        self.assertEqual(report["tracer_drafts_generated"], 2)
        self.assertEqual(report["brands_processed"], 2)
        self.assertIn("minimax", report["llm_providers_used"])
        self.assertTrue(any("Evidence: https://www.amazon.com/stores/brand" in body for _, lead in fake_storage.upserts for body in [lead.get("draft_preview_body", "")]))
        self.assertTrue(any("amazon.com/stores/brand" in body for _, lead in fake_storage.upserts for body in [lead.get("draft_preview_body", "")]))
        self.assertTrue(report["tracer_report_path"])
        self.assertTrue(str(report["tracer_log_path"]).endswith("tracer_run.log"))
        self.assertTrue(str(report["tracer_summary_path"]).endswith("tracer_bullet_summary.md"))
        self.assertGreaterEqual(len(fake_storage.reports), 1)
        self.assertGreaterEqual(fake_storage.commits, 1)

    @patch("amazon_lead_agent.runtime.verify_amazon_evidence")
    @patch("amazon_lead_agent.runtime.extract_brand_profile")
    @patch("amazon_lead_agent.runtime.get_storage_router")
    def test_tracer_blocks_draft_when_structured_evidence_url_is_missing(self, mock_get_storage_router, mock_extract_brand_profile, mock_verify_amazon_evidence) -> None:
        class FakeStorage:
            uses_sheets = True

            def __init__(self):
                self.upserts = []
                self.events = []
                self.reports = []
                self.commits = 0

            def get_leads_for_enrichment(self, limit):
                return [{
                    "id": "lead-1",
                    "lead_id": "lead-1",
                    "company_name": "Tatcha",
                    "brand_name": "Tatcha",
                    "website": "https://www.tatcha.com",
                    "category": "beauty",
                    "status": "needs_enrichment",
                    "source_urls": ["https://www.tatcha.com"],
                }]

            def upsert_lead(self, lead, tab=None):
                self.upserts.append((tab, dict(lead)))
                return lead.get("id", "lead-1")

            def record_outreach_event(self, event):
                self.events.append(dict(event))

            def append_daily_report(self, report):
                self.reports.append(dict(report))

            def commit(self):
                self.commits += 1

            def close(self):
                return None

        mock_get_storage_router.return_value = FakeStorage()
        mock_extract_brand_profile.return_value = {
            "company_name": "Luxury Japanese Skincare Products",
            "brand_name": "Luxury Japanese Skincare Products",
            "website": "https://www.tatcha.com",
            "website_title": "Luxury Japanese Skincare Products | Tatcha",
            "category": "beauty",
            "public_emails": ["hello@example.com"],
            "contact_page_url": "https://www.tatcha.com/contact",
            "founder_or_executive_names": [],
            "ecommerce_or_marketplace_people": [],
            "pain_points": [],
            "source_quotes": [],
            "source_urls": ["https://www.tatcha.com"],
            "confidence": 0.5,
            "extraction_method": "heuristic_fallback",
        }
        mock_verify_amazon_evidence.return_value = {
            "canonical_brand_name": "Tatcha",
            "website_title": "Luxury Japanese Skincare Products | Tatcha",
            "structured_evidence_found": True,
            "weak_text_signal_found": False,
            "best_evidence_url": "",
            "best_evidence_title": "Tatcha Official Store",
            "best_evidence_snippet": "Visit the Tatcha Store",
            "best_evidence_source": "search_index | site:amazon.com/stores \"Tatcha\"",
            "best_evidence_confidence": "high",
            "best_evidence_type": "amazon_storefront_search_result",
            "best_evidence_reason": "Amazon storefront/store page matched",
            "amazon_evidence_items": [],
            "amazon_evidence_urls": [],
            "amazon_backlink_found": False,
            "amazon_evidence_summary": "",
            "amazon_queries_run": [],
            "amazon_search_results_seen": 0,
            "amazon_results_rejected_count": 0,
            "amazon_results_rejected_reasons": [],
            "search_stats": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                report = run_campaign(
                    {
                        "storage": {"google_sheet_id": "sheet-123"},
                        "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10, "daily_discovery_limit": 8, "categories": ["beauty"]},
                        "sender": {"name": "Zaigham Ali", "offer": "Offer"},
                        "llm": {"provider": "minimax"},
                    },
                    cwd / "leads.db",
                    mode="tracer",
                    dry_run=True,
                )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(report["tracer_drafts_generated"], 0)
        self.assertEqual(report["tracer_drafts_blocked_due_missing_evidence"], 1)
        self.assertTrue(any(lead.get("draft_block_reason") == "missing_structured_amazon_evidence_url" for _, lead in mock_get_storage_router.return_value.upserts))

    @patch("amazon_lead_agent.runtime.verify_amazon_evidence")
    @patch("amazon_lead_agent.runtime.extract_brand_profile")
    @patch("amazon_lead_agent.runtime.get_storage_router")
    def test_tracer_blocks_draft_when_best_evidence_is_not_amazon(self, mock_get_storage_router, mock_extract_brand_profile, mock_verify_amazon_evidence) -> None:
        class FakeStorage:
            uses_sheets = True

            def __init__(self):
                self.upserts = []
                self.events = []
                self.reports = []
                self.commits = 0

            def get_leads_for_enrichment(self, limit):
                return [{
                    "id": "lead-1",
                    "lead_id": "lead-1",
                    "company_name": "Glossier",
                    "brand_name": "Glossier",
                    "website": "https://www.glossier.com",
                    "category": "beauty",
                    "status": "needs_enrichment",
                    "source_urls": ["https://www.glossier.com"],
                }]

            def upsert_lead(self, lead, tab=None):
                self.upserts.append((tab, dict(lead)))
                return lead.get("id", "lead-1")

            def record_outreach_event(self, event):
                self.events.append(dict(event))

            def append_daily_report(self, report):
                self.reports.append(dict(report))

            def commit(self):
                self.commits += 1

            def close(self):
                return None

        fake_storage = FakeStorage()
        mock_get_storage_router.return_value = fake_storage
        mock_extract_brand_profile.return_value = {
            "company_name": "Glossier",
            "brand_name": "Glossier",
            "website": "https://www.glossier.com",
            "website_title": "Glossier",
            "category": "beauty",
            "public_emails": ["hello@example.com"],
            "contact_page_url": "https://www.glossier.com/contact",
            "founder_or_executive_names": [],
            "ecommerce_or_marketplace_people": [],
            "pain_points": [],
            "source_quotes": [],
            "source_urls": ["https://www.glossier.com"],
            "confidence": 0.5,
            "extraction_method": "heuristic_fallback",
        }
        mock_verify_amazon_evidence.return_value = {
            "canonical_brand_name": "Glossier",
            "website_title": "Glossier",
            "structured_evidence_found": True,
            "weak_text_signal_found": False,
            "best_evidence_url": "https://www.glossier.com",
            "best_evidence_title": "Glossier",
            "best_evidence_snippet": "Brand page",
            "best_evidence_source": "manual_sheet_override",
            "best_evidence_confidence": "high",
            "best_evidence_type": "manual_verified_amazon_url",
            "best_evidence_reason": "manual override",
            "amazon_evidence_items": [],
            "amazon_evidence_urls": [],
            "amazon_backlink_found": False,
            "amazon_evidence_summary": "",
            "amazon_queries_run": [],
            "amazon_search_results_seen": 0,
            "amazon_results_rejected_count": 0,
            "amazon_results_rejected_reasons": [],
            "search_stats": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                report = run_campaign(
                    {
                        "storage": {"google_sheet_id": "sheet-123"},
                        "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10, "daily_discovery_limit": 8, "categories": ["beauty"]},
                        "sender": {"name": "Zaigham Ali", "offer": "Offer"},
                        "llm": {"provider": "minimax"},
                    },
                    cwd / "leads.db",
                    mode="tracer",
                    dry_run=True,
                )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(report["tracer_drafts_generated"], 0)
        self.assertEqual(report["tracer_drafts_blocked_due_missing_evidence"], 1)
        self.assertTrue(any(lead.get("draft_block_reason") == "missing_structured_amazon_evidence_url" for _, lead in fake_storage.upserts))


if __name__ == "__main__":
    unittest.main()
