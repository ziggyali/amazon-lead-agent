import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amazon_lead_agent.tools.sheet_store import SheetStore
from amazon_lead_agent.tools.storage_router import StorageRouter, get_storage_router


class FakeSheetStore:
    def __init__(self, sheet_id, auth_mode=None):
        self.sheet_id = sheet_id
        self.auth_mode = auth_mode
        self.warmed = False

    def warm_tabs(self, tabs=None):
        self.warmed = True

    def snapshot(self):
        return {"sheet_id": self.sheet_id, "lead_count": 0, "dirty_tabs": [], "tabs": {}}


class StorageRouterTests(unittest.TestCase):
    @patch("amazon_lead_agent.tools.storage_router.SheetStore", FakeSheetStore)
    def test_sheet_mode_uses_sheet_store_when_sheet_id_is_present(self) -> None:
        config = {
            "storage": {
                "storage_mode": "sheets",
                "local_cache_enabled": False,
                "google_sheet_id": "sheet-123",
                "sqlite_path": "data/leads.db",
            }
        }
        router = get_storage_router(config, Path("data/leads.db"))
        self.assertTrue(router.uses_sheets)
        self.assertFalse(router.uses_sqlite)
        self.assertEqual(router.mode, "sheets")
        self.assertEqual(router.snapshot()["sheet_id"], "sheet-123")

    @patch("amazon_lead_agent.tools.storage_router.SheetStore", FakeSheetStore)
    def test_missing_sheet_id_falls_back_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            config = {
                "storage": {
                    "storage_mode": "sheets",
                    "local_cache_enabled": False,
                    "google_sheet_id": "",
                    "sqlite_path": str(db_path),
                }
            }
            router = get_storage_router(config, db_path)
            self.assertFalse(router.uses_sheets)
            self.assertTrue(router.uses_sqlite)
            self.assertEqual(router.mode, "sqlite")
            router.close()

    @patch("amazon_lead_agent.tools.google_sheets.read_tab_rows", return_value=[{"id": "lead-1", "company_name": "Acme", "website": "https://example.com"}])
    @patch("amazon_lead_agent.tools.google_sheets.read_tab_headers", return_value=["id", "company_name", "website", "status", "updated_at"])
    @patch("amazon_lead_agent.tools.google_sheets.append_rows", return_value={"confirmed_rows": 1})
    def test_sheet_store_batches_writes_until_commit(self, mock_append_rows, mock_read_headers, mock_read_rows) -> None:
        store = SheetStore("sheet-123")
        lead = {"id": "lead-1", "company_name": "Acme", "website": "https://example.com", "status": "discovered"}
        store.upsert_lead(lead)
        self.assertEqual(mock_append_rows.call_count, 0)
        store.commit()
        self.assertEqual(mock_append_rows.call_count, 1)
        self.assertEqual(store.lead_queue_rows_written, 1)
        self.assertEqual(store.lead_queue_rows_queued, 1)
        self.assertEqual(store.lead_queue_rows_failed, 0)
        self.assertEqual(store.lead_queue_verification_status, "confirmed")
        self.assertEqual(store.storage_flush_status, "ok")

    def test_sheet_store_retries_connection_errors(self) -> None:
        store = SheetStore("sheet-123")
        attempts = {"count": 0}

        def writer():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OSError(10060, "timed out")
            return None

        with patch("time.sleep", return_value=None):
            store._write_with_backoff(writer, label="Lead Queue:lead")
        self.assertEqual(attempts["count"], 2)

    @patch("amazon_lead_agent.tools.google_sheets.read_tab_headers", return_value=["id", "company_name", "website", "status", "updated_at"])
    @patch("amazon_lead_agent.tools.google_sheets.read_tab_rows", return_value=[{"id": "lead-1", "company_name": "Acme", "website": "https://example.com"}])
    @patch("amazon_lead_agent.tools.google_sheets.append_rows", return_value={"confirmed_rows": 1})
    def test_flush_skips_verification_error_without_lying_about_success(self, mock_append_rows, mock_read_rows, mock_read_headers) -> None:
        store = SheetStore("sheet-123")
        store.upsert_lead({"id": "lead-1", "company_name": "Acme", "website": "https://example.com", "status": "discovered"})
        with patch("amazon_lead_agent.tools.google_sheets.read_tab_rows", side_effect=[[], OSError(10060, "timed out")]):
            store.commit()
        self.assertEqual(store.lead_queue_rows_written, 1)
        self.assertEqual(store.lead_queue_rows_failed, 0)
        self.assertEqual(store.lead_queue_verification_status, "skipped_due_to_read_error")
        self.assertEqual(store.storage_flush_status, "ok")
        self.assertFalse(store.dedupe_cache_unavailable)

    @patch("amazon_lead_agent.tools.google_sheets.read_tab_headers", return_value=["id", "company_name", "website", "status", "updated_at"])
    @patch("amazon_lead_agent.tools.google_sheets.read_tab_rows", return_value=[])
    @patch("amazon_lead_agent.tools.google_sheets.append_rows", side_effect=ValueError("append failed"))
    def test_append_api_failure_increments_failed_count(self, mock_append_rows, mock_read_rows, mock_read_headers) -> None:
        store = SheetStore("sheet-123")
        store.upsert_lead({"id": "lead-1", "company_name": "Acme", "website": "https://example.com", "status": "discovered"})
        store.commit()
        self.assertEqual(store.lead_queue_rows_written, 0)
        self.assertEqual(store.lead_queue_rows_failed, 1)
        self.assertIn(store.storage_flush_status, {"failed", "partial_failed"})
        self.assertTrue(store.flush_errors)

    @patch("amazon_lead_agent.tools.google_sheets.read_tab_headers", return_value=["id", "company_name", "website", "status", "updated_at"])
    @patch("amazon_lead_agent.tools.google_sheets.read_tab_rows", side_effect=OSError(10060, "timed out"))
    @patch("amazon_lead_agent.tools.google_sheets.append_rows", return_value={"confirmed_rows": 1})
    def test_dedupe_cache_unavailable_is_reported_when_lead_cache_read_fails(self, mock_append_rows, mock_read_rows, mock_read_headers) -> None:
        store = SheetStore("sheet-123")
        store.upsert_lead({"id": "lead-1", "company_name": "Acme", "website": "https://example.com", "status": "discovered"})
        store.commit()
        self.assertTrue(store.dedupe_cache_unavailable)
        self.assertEqual(store.lead_queue_rows_written, 1)
        self.assertEqual(store.lead_queue_verification_status, "skipped_due_to_read_error")

    @patch(
        "amazon_lead_agent.tools.google_sheets.read_tab_rows",
        return_value=[
            {"id": f"lead-{index}", "company_name": f"Brand {index}", "website": f"https://brand{index}.com"}
            for index in range(1, 9)
        ],
    )
    @patch("amazon_lead_agent.tools.google_sheets.read_tab_headers", return_value=["id", "company_name", "website", "status", "updated_at"])
    @patch("amazon_lead_agent.tools.google_sheets.append_rows", return_value={"confirmed_rows": 8})
    def test_direct_seed_discovery_dry_run_persists_confirmed_rows(self, mock_append_rows, mock_read_rows, mock_read_headers) -> None:
        store = SheetStore("sheet-123")
        for index in range(1, 9):
            store.upsert_lead(
                {
                    "id": f"lead-{index}",
                    "company_name": f"Brand {index}",
                    "website": f"https://brand{index}.com",
                    "status": "needs_enrichment",
                }
            )
        store.commit()
        self.assertEqual(store.lead_queue_rows_queued, 8)
        self.assertEqual(store.lead_queue_rows_written, 8)
        self.assertEqual(store.lead_queue_rows_failed, 0)


if __name__ == "__main__":
    unittest.main()
