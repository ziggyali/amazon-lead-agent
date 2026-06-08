import os
import unittest
from unittest.mock import patch

from amazon_lead_agent.tools import google_sheets


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


if __name__ == "__main__":
    unittest.main()
