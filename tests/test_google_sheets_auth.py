import os
import unittest
from unittest.mock import patch

from amazon_lead_agent.tools import google_sheets
from amazon_lead_agent.tools.sheet_store import SheetStore


class _FakeValuesGet:
    def __init__(self, values):
        self._values = values

    def execute(self):
        return {"values": self._values}


class _FakeValues:
    def __init__(self, values):
        self._values = values

    def get(self, spreadsheetId=None, range=None):  # noqa: N803
        return _FakeValuesGet(self._values)


class _FakeSpreadsheets:
    def __init__(self, values):
        self._values = values

    def values(self):
        return _FakeValues(self._values)


class _FakeService:
    def __init__(self, values):
        self._values = values

    def spreadsheets(self):
        return _FakeSpreadsheets(self._values)


class GoogleSheetsAuthTests(unittest.TestCase):
    @patch.object(google_sheets, "_load_oauth_credentials", return_value="oauth-creds")
    @patch.object(google_sheets, "_load_service_account_credentials", return_value="service-creds")
    def test_explicit_oauth_mode_uses_oauth(self, mock_service, mock_oauth) -> None:
        with patch.dict(os.environ, {"GOOGLE_SHEETS_AUTH_MODE": "oauth"}, clear=False):
            creds = google_sheets._load_credentials()
        self.assertEqual(creds, "oauth-creds")
        mock_oauth.assert_called_once()
        mock_service.assert_not_called()

    @patch.object(google_sheets, "_load_oauth_credentials", return_value="oauth-creds")
    @patch.object(google_sheets, "_load_service_account_credentials", side_effect=RuntimeError("service failed"))
    @patch.object(google_sheets, "_service_account_configured", return_value=True)
    @patch.object(google_sheets, "_oauth_configured", return_value=True)
    def test_auto_falls_back_to_oauth(self, mock_oauth_configured, mock_service_configured, mock_service, mock_oauth) -> None:
        with patch.dict(os.environ, {"GOOGLE_SHEETS_AUTH_MODE": "auto"}, clear=False):
            creds = google_sheets._load_credentials()
        self.assertEqual(creds, "oauth-creds")
        mock_service.assert_called_once()
        mock_oauth.assert_called_once()
        mock_service_configured.assert_called_once()
        mock_oauth_configured.assert_called_once()

    def test_read_tab_rows_retries_transient_connection_error(self) -> None:
        google_sheets.reset_io_stats()
        service = _FakeService([["id", "company_name"], ["lead-1", "Acme"]])
        with patch.object(google_sheets, "_build_service", side_effect=[OSError(10060, "timed out"), service]), patch("time.sleep", return_value=None):
            rows = google_sheets.read_tab_rows("sheet-123", "Lead Queue")
        self.assertEqual(rows, [{"id": "lead-1", "company_name": "Acme"}])
        stats = google_sheets.get_io_stats()
        self.assertEqual(stats["sheet_read_retry_count"], 1)
        self.assertEqual(stats["sheet_connection_error_count"], 1)
        self.assertEqual(stats["sheet_read_error_count"], 1)
        self.assertEqual(len(stats["failed_sheet_reads"]), 1)

    def test_get_all_leads_continues_after_transient_tab_failure(self) -> None:
        store = SheetStore("sheet-123")
        with patch.object(google_sheets, "read_tab_rows", side_effect=[[], [{"id": "lead-1", "company_name": "Acme", "website": "https://example.com"}], [], []]):
            rows = store.get_all_leads()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "lead-1")


if __name__ == "__main__":
    unittest.main()
