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
                    queued = len(self.upserts)
                    return {
                        "sheet_mirror_error_count": self.sheet_mirror_error_count,
                        "failed_sheet_rows": self.failed_sheet_rows,
                        "sheet_store": {
                            "lead_queue_rows_queued": queued,
                            "lead_queue_rows_attempted": queued,
                            "lead_queue_rows_written": queued,
                            "lead_queue_rows_failed": 0,
                            "lead_queue_verified_count": queued,
                            "lead_queue_missing_after_write": 0,
                            "lead_queue_verification_status": "confirmed",
                            "dedupe_cache_unavailable": False,
                            "storage_flush_status": "ok",
                        },
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
                        "rejected_likely_brand_filter_count": 2,
                        "rejected_due_to_no_amazon_evidence_count": 0,
                        "discovered_count_by_category": {"beauty": 1},
                        "query_budget_used": 2,
                        "query_budget_remaining": 14,
                        "discovery_runtime_seconds": 1.25,
                        "stopped_reason": "accepted_limit_reached",
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
            self.assertEqual(report["rejected_likely_brand_filter_count"], 2)
            self.assertEqual(report["discovered_count_by_category"], {"beauty": 1})
            self.assertEqual(report["query_budget_used"], 2)
            self.assertEqual(report["query_budget_remaining"], 14)
            self.assertEqual(report["stopped_reason"], "accepted_limit_reached")

    @patch("amazon_lead_agent.runtime.get_storage_router")
    @patch("amazon_lead_agent.runtime.run_outreach", return_value=[])
    @patch("amazon_lead_agent.runtime.run_scoring")
    @patch("amazon_lead_agent.runtime.run_extraction")
    @patch("amazon_lead_agent.runtime.run_discovery")
    def test_discover_dry_run_persists_candidates_and_flushes_storage(
        self,
        mock_discovery,
        mock_extraction,
        mock_scoring,
        mock_outreach,
        mock_get_storage_router,
    ) -> None:
        class FakeStorage:
            uses_sheets = True

            def __init__(self):
                self.rows_by_id = {}
                self.upsert_calls = []
                self.commit_calls = 0
                self.failed_sheet_rows = []
                self.sheet_mirror_error_count = 0
                self.sheet_flush_errors = []
                self.sheet_read_error_count = 0
                self.sheet_read_retry_count = 0
                self.sheet_connection_error_count = 0
                self.failed_sheet_reads = []

            def upsert_lead(self, lead, tab=None):
                lead_id = lead.get("id") or f"lead-{len(self.upsert_calls)+1}"
                stored = dict(lead)
                stored["id"] = lead_id
                self.rows_by_id[lead_id] = stored
                self.upsert_calls.append((tab, stored))
                return lead_id

            def record_outreach_event(self, event):
                return None

            def get_leads_for_enrichment(self, limit):
                return []

            def get_leads_for_scoring(self, limit):
                return []

            def get_leads_for_drafting(self, min_score, limit):
                return []

            def append_daily_report(self, report):
                return None

            def snapshot(self):
                queued = len(self.rows_by_id)
                return {
                    "sheet_mirror_error_count": self.sheet_mirror_error_count,
                    "failed_sheet_rows": list(self.failed_sheet_rows),
                    "sheet_flush_errors": list(self.sheet_flush_errors),
                    "sheet_read_error_count": self.sheet_read_error_count,
                    "sheet_read_retry_count": self.sheet_read_retry_count,
                    "sheet_connection_error_count": self.sheet_connection_error_count,
                    "failed_sheet_reads": list(self.failed_sheet_reads),
                    "sheet_store": {
                        "lead_queue_rows_queued": queued,
                        "lead_queue_rows_attempted": queued,
                        "lead_queue_rows_written": queued,
                        "lead_queue_rows_failed": 0,
                        "lead_queue_verified_count": queued,
                        "lead_queue_missing_after_write": 0,
                        "lead_queue_verification_status": "confirmed",
                        "dedupe_cache_unavailable": False,
                        "storage_flush_status": "ok",
                    },
                    "uses_sheets": True,
                    "mode": "sheets",
                }

            def commit(self):
                self.commit_calls += 1
                return None

            def close(self):
                return None

        fake_storage = FakeStorage()
        mock_get_storage_router.return_value = fake_storage
        discovered = [
            {
                "id": "lead-1",
                "company_name": "Glossier",
                "website": "https://www.glossier.com",
                "category": "beauty",
                "status": "needs_enrichment",
                "source_urls": ["https://www.glossier.com"],
            },
            {
                "id": "lead-2",
                "company_name": "Tatcha",
                "website": "https://www.tatcha.com",
                "category": "beauty",
                "status": "needs_enrichment",
                "source_urls": ["https://www.tatcha.com"],
            },
        ]

        def discover_side_effect(config, storage):
            for lead in discovered:
                storage.upsert_lead(lead, tab="Lead Queue")
            return {
                "leads": discovered,
                "search_stats": {
                    "provider_counts": {"seeded": 2},
                    "blocked_query_counts": {},
                    "rate_limited_query_counts": {},
                    "rejected_content_domain_count": 0,
                    "rejected_listicle_domains_count": 0,
                    "cleaned_redirect_count": 0,
                    "rejected_redirect_count": 0,
                    "hard_rejected_junk_count": 0,
                    "soft_pass_needs_enrichment_count": 2,
                    "rejected_likely_brand_filter_count": 0,
                    "rejected_due_to_no_amazon_evidence_count": 0,
                    "discovered_count_by_category": {"beauty": 2},
                    "query_budget_used": 0,
                    "query_budget_remaining": 8,
                    "discovery_runtime_seconds": 0.01,
                    "stopped_reason": "accepted_limit_reached",
                },
            }

        mock_discovery.side_effect = discover_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            report = run_campaign(
                {
                    "storage": {"google_sheet_id": "sheet-123"},
                    "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10, "daily_discovery_limit": 2},
                    "sender": {"name": "Zaigham Ali", "offer": "Offer"},
                },
                db_path,
                mode="discover",
                dry_run=True,
            )

        self.assertEqual(len(fake_storage.upsert_calls), 2)
        self.assertEqual(set(fake_storage.rows_by_id), {"lead-1", "lead-2"})
        self.assertGreaterEqual(fake_storage.commit_calls, 1)
        self.assertEqual(report["discovered_count"], 2)
        self.assertEqual(report["discovered_persisted_count"], 2)
        self.assertEqual(report["discovered_persist_failed_count"], 0)
        self.assertEqual(report["lead_queue_rows_written"], 2)
        self.assertEqual(report["storage_mode_used"], "sheets")
        self.assertEqual(report["storage_flush_status"], "ok")
        self.assertEqual(report["drafts_created"], 0)
        mock_outreach.assert_not_called()

    @patch("amazon_lead_agent.runtime.get_storage_router")
    @patch("amazon_lead_agent.runtime.run_outreach", return_value=[])
    @patch("amazon_lead_agent.runtime.run_scoring")
    @patch("amazon_lead_agent.runtime.run_extraction")
    @patch("amazon_lead_agent.runtime.run_discovery")
    def test_duplicate_seed_discovery_updates_existing_row_not_duplicate_append(
        self,
        mock_discovery,
        mock_extraction,
        mock_scoring,
        mock_outreach,
        mock_get_storage_router,
    ) -> None:
        class FakeStorage:
            uses_sheets = True

            def __init__(self):
                self.rows_by_id = {}
                self.upsert_calls = []
                self.commit_calls = 0
                self.failed_sheet_rows = []
                self.sheet_mirror_error_count = 0
                self.sheet_flush_errors = []
                self.sheet_read_error_count = 0
                self.sheet_read_retry_count = 0
                self.sheet_connection_error_count = 0
                self.failed_sheet_reads = []

            def upsert_lead(self, lead, tab=None):
                lead_id = lead.get("id") or "lead-1"
                stored = dict(lead)
                stored["id"] = lead_id
                self.rows_by_id[lead_id] = stored
                self.upsert_calls.append((tab, stored))
                return lead_id

            def record_outreach_event(self, event):
                return None

            def get_leads_for_enrichment(self, limit):
                return []

            def get_leads_for_scoring(self, limit):
                return []

            def get_leads_for_drafting(self, min_score, limit):
                return []

            def append_daily_report(self, report):
                return None

            def snapshot(self):
                queued = len(self.rows_by_id)
                return {
                    "sheet_mirror_error_count": self.sheet_mirror_error_count,
                    "failed_sheet_rows": list(self.failed_sheet_rows),
                    "sheet_flush_errors": list(self.sheet_flush_errors),
                    "sheet_read_error_count": self.sheet_read_error_count,
                    "sheet_read_retry_count": self.sheet_read_retry_count,
                    "sheet_connection_error_count": self.sheet_connection_error_count,
                    "failed_sheet_reads": list(self.failed_sheet_reads),
                    "sheet_store": {
                        "lead_queue_rows_queued": queued,
                        "lead_queue_rows_attempted": queued,
                        "lead_queue_rows_written": queued,
                        "lead_queue_rows_failed": 0,
                        "lead_queue_verified_count": queued,
                        "lead_queue_missing_after_write": 0,
                        "lead_queue_verification_status": "confirmed",
                        "dedupe_cache_unavailable": False,
                        "storage_flush_status": "ok",
                    },
                    "uses_sheets": True,
                    "mode": "sheets",
                }

            def commit(self):
                self.commit_calls += 1
                return None

            def close(self):
                return None

        fake_storage = FakeStorage()
        mock_get_storage_router.return_value = fake_storage
        def duplicate_discover_side_effect(config, storage):
            lead = {
                "id": "lead-dup",
                "company_name": "Glossier",
                "website": "https://www.glossier.com",
                "category": "beauty",
                "status": "needs_enrichment",
                "source_urls": ["https://www.glossier.com"],
            }
            storage.upsert_lead(lead, tab="Lead Queue")
            storage.upsert_lead(lead, tab="Lead Queue")
            return {
                "leads": [lead, lead],
                "search_stats": {
                    "provider_counts": {"seeded": 2},
                    "blocked_query_counts": {},
                    "rate_limited_query_counts": {},
                    "rejected_content_domain_count": 0,
                    "rejected_listicle_domains_count": 0,
                    "cleaned_redirect_count": 0,
                    "rejected_redirect_count": 0,
                    "hard_rejected_junk_count": 0,
                    "soft_pass_needs_enrichment_count": 2,
                    "rejected_likely_brand_filter_count": 0,
                    "rejected_due_to_no_amazon_evidence_count": 0,
                    "discovered_count_by_category": {"beauty": 2},
                    "query_budget_used": 0,
                    "query_budget_remaining": 8,
                    "discovery_runtime_seconds": 0.01,
                    "stopped_reason": "accepted_limit_reached",
                },
            }

        mock_discovery.side_effect = duplicate_discover_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            report = run_campaign(
                {
                    "storage": {"google_sheet_id": "sheet-123"},
                    "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10, "daily_discovery_limit": 2},
                    "sender": {"name": "Zaigham Ali", "offer": "Offer"},
                },
                db_path,
                mode="discover",
                dry_run=True,
            )

        self.assertEqual(len(fake_storage.rows_by_id), 1)
        self.assertEqual(len(fake_storage.upsert_calls), 2)
        self.assertEqual(report["discovered_persisted_count"], 1)
        self.assertEqual(report["discovered_persist_failed_count"], 0)

    @patch("amazon_lead_agent.runtime.get_storage_router")
    @patch("amazon_lead_agent.runtime.run_outreach", return_value=[])
    @patch("amazon_lead_agent.runtime.run_scoring")
    @patch("amazon_lead_agent.runtime.run_extraction")
    @patch("amazon_lead_agent.runtime.run_discovery")
    def test_persistence_failure_appears_in_report(
        self,
        mock_discovery,
        mock_extraction,
        mock_scoring,
        mock_outreach,
        mock_get_storage_router,
    ) -> None:
        class FailingStorage:
            uses_sheets = True

            def __init__(self):
                self.commit_calls = 0
                self.failed_sheet_rows = []
                self.sheet_mirror_error_count = 0
                self.sheet_flush_errors = []
                self.sheet_read_error_count = 0
                self.sheet_read_retry_count = 0
                self.sheet_connection_error_count = 0
                self.failed_sheet_reads = []

            def upsert_lead(self, lead, tab=None):
                self.failed_sheet_rows.append({"tab": tab or "Lead Queue", "lead_id": lead.get("id", ""), "error": "write failed"})
                raise ValueError("write failed")

            def record_outreach_event(self, event):
                return None

            def get_leads_for_enrichment(self, limit):
                return []

            def get_leads_for_scoring(self, limit):
                return []

            def get_leads_for_drafting(self, min_score, limit):
                return []

            def append_daily_report(self, report):
                return None

            def snapshot(self):
                return {
                    "sheet_mirror_error_count": self.sheet_mirror_error_count,
                    "failed_sheet_rows": list(self.failed_sheet_rows),
                    "sheet_flush_errors": list(self.sheet_flush_errors),
                    "sheet_read_error_count": self.sheet_read_error_count,
                    "sheet_read_retry_count": self.sheet_read_retry_count,
                    "sheet_connection_error_count": self.sheet_connection_error_count,
                    "failed_sheet_reads": list(self.failed_sheet_reads),
                    "sheet_store": {
                        "lead_queue_rows_queued": 1,
                        "lead_queue_rows_attempted": 1,
                        "lead_queue_rows_written": 0,
                        "lead_queue_rows_failed": 1,
                        "lead_queue_verified_count": 0,
                        "lead_queue_missing_after_write": 0,
                        "lead_queue_verification_status": "failed",
                        "dedupe_cache_unavailable": False,
                        "storage_flush_status": "failed",
                    },
                    "uses_sheets": True,
                    "mode": "sheets",
                }

            def commit(self):
                self.commit_calls += 1
                return None

            def close(self):
                return None

        mock_get_storage_router.return_value = FailingStorage()
        mock_discovery.return_value = {
            "leads": [
                {
                    "id": "lead-1",
                    "company_name": "Glossier",
                    "website": "https://www.glossier.com",
                    "category": "beauty",
                    "status": "needs_enrichment",
                    "source_urls": ["https://www.glossier.com"],
                }
            ],
            "search_stats": {
                "provider_counts": {"seeded": 1},
                "blocked_query_counts": {},
                "rate_limited_query_counts": {},
                "rejected_content_domain_count": 0,
                "rejected_listicle_domains_count": 0,
                "cleaned_redirect_count": 0,
                "rejected_redirect_count": 0,
                "hard_rejected_junk_count": 0,
                "soft_pass_needs_enrichment_count": 1,
                "rejected_likely_brand_filter_count": 0,
                "rejected_due_to_no_amazon_evidence_count": 0,
                "discovered_count_by_category": {"beauty": 1},
                "query_budget_used": 0,
                "query_budget_remaining": 8,
                "discovery_runtime_seconds": 0.01,
                "stopped_reason": "accepted_limit_reached",
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            report = run_campaign(
                {
                    "storage": {"google_sheet_id": "sheet-123"},
                    "campaign": {"minimum_score_for_draft": 75, "daily_draft_limit": 10, "daily_discovery_limit": 1},
                    "sender": {"name": "Zaigham Ali", "offer": "Offer"},
                },
                db_path,
                mode="discover",
                dry_run=True,
            )

        self.assertGreater(report["discovered_persist_failed_count"], 0)
        self.assertEqual(report["lead_queue_rows_written"], 0)
        self.assertEqual(report["drafts_created"], 0)

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
                    self.sheet_read_error_count = 2
                    self.sheet_read_retry_count = 3
                    self.sheet_connection_error_count = 2
                    self.failed_sheet_reads = [{"sheet_id": "sheet-123", "tab": "Lead Queue", "error": "timeout"}]

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
                        "sheet_read_error_count": self.sheet_read_error_count,
                        "sheet_read_retry_count": self.sheet_read_retry_count,
                        "sheet_connection_error_count": self.sheet_connection_error_count,
                        "failed_sheet_reads": self.failed_sheet_reads,
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
            self.assertEqual(report["sheet_read_error_count"], 2)
            self.assertEqual(report["sheet_read_retry_count"], 3)
            self.assertEqual(report["sheet_connection_error_count"], 2)
            self.assertEqual(report["failed_sheet_reads"], [{"sheet_id": "sheet-123", "tab": "Lead Queue", "error": "timeout"}])
            self.assertTrue(report["failed_sheet_rows"])
            self.assertTrue(Path(tmpdir, "campaign_report.md").exists())
            self.assertTrue(report["campaign_report_path"])


if __name__ == "__main__":
    unittest.main()
