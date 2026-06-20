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
        self.assertTrue(any("amazon.com/stores/brand" in body for _, lead in fake_storage.upserts for body in [lead.get("draft_preview_body", "")]))
        self.assertTrue(report["tracer_report_path"])
        self.assertTrue(report["tracer_summary_path"])
        self.assertGreaterEqual(len(fake_storage.reports), 1)
        self.assertGreaterEqual(fake_storage.commits, 1)


if __name__ == "__main__":
    unittest.main()
