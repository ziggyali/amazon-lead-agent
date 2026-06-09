import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
