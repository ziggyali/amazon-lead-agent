import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amazon_lead_agent.tools.amazon_evidence_verification import _normalize_amazon_url, verify_amazon_evidence


class AmazonVerificationDebugTests(unittest.TestCase):
    def _tmp_cwd(self):
        return tempfile.TemporaryDirectory()

    @patch("amazon_lead_agent.tools.amazon_evidence_verification.search_web_with_metadata")
    def test_debug_jsonl_written_with_rejected_reason(self, mock_search) -> None:
        mock_search.return_value = ([], "duckduckgo", "blocked")
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                result = verify_amazon_evidence(
                    {
                        "seed_label": "Tatcha",
                        "company_name": "Tatcha",
                        "website": "https://www.tatcha.com",
                        "category": "beauty",
                    },
                    search_limit=1,
                )
            finally:
                os.chdir(old_cwd)

            debug_path = Path(tmpdir) / "logs" / "amazon_verification_debug.jsonl"
            self.assertTrue(debug_path.exists())
            records = [json.loads(line) for line in debug_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(records)
            self.assertTrue(any(record.get("decision") == "rejected" for record in records))
            self.assertTrue(any(record.get("provider") == "duckduckgo" for record in records))
            self.assertFalse(result["structured_evidence_found"])

    @patch("amazon_lead_agent.tools.amazon_evidence_verification.search_web_with_metadata")
    def test_cached_prior_verified_url_is_reused_when_fresh_search_finds_nothing(self, mock_search) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                mock_search.return_value = (
                    [
                        {
                            "url": "https://www.amazon.com/Glossier-Parfum-Travel-Spray-0-27/dp/B0DNCMDLSY",
                            "title": "Glossier You Parfum Travel Spray",
                            "snippet": "by Glossier",
                            "provider": "bing_html",
                        }
                    ],
                    "bing_html",
                    "ok",
                )
                first = verify_amazon_evidence(
                    {
                        "seed_label": "Glossier",
                        "company_name": "Glossier",
                        "website": "https://www.glossier.com",
                        "category": "beauty",
                    },
                    search_limit=1,
                )
                self.assertTrue(first["structured_evidence_found"])
                self.assertEqual(first["best_evidence_type"], "amazon_product_search_result")

                mock_search.return_value = ([], "duckduckgo", "empty")
                second = verify_amazon_evidence(
                    {
                        "seed_label": "Glossier",
                        "company_name": "Glossier",
                        "website": "https://www.glossier.com",
                        "category": "beauty",
                    },
                    search_limit=1,
                )
            finally:
                os.chdir(old_cwd)

            self.assertTrue(second["structured_evidence_found"])
            self.assertEqual(second["best_evidence_type"], "cached_verified_amazon_url")
            self.assertEqual(second["best_evidence_source"], "cached_prior_verified")
            self.assertEqual(second["best_evidence_url"], "https://www.amazon.com/Glossier-Parfum-Travel-Spray-0-27/dp/B0DNCMDLSY")
            self.assertIn("https://www.amazon.com/Glossier-Parfum-Travel-Spray-0-27/dp/B0DNCMDLSY", second["amazon_evidence_urls"])

    @patch("amazon_lead_agent.tools.amazon_evidence_verification.search_web_with_metadata")
    def test_manual_amazon_evidence_url_overrides_search_failure(self, mock_search) -> None:
        mock_search.return_value = ([], "duckduckgo", "empty")
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                result = verify_amazon_evidence(
                    {
                        "seed_label": "Brooklinen",
                        "company_name": "Brooklinen",
                        "website": "https://www.brooklinen.com",
                        "category": "home",
                        "manual_amazon_evidence_url": "https://www.amazon.com/stores/Brooklinen/page/123",
                        "manual_amazon_evidence_notes": "Manually verified",
                    },
                    search_limit=1,
                )
            finally:
                os.chdir(old_cwd)

            self.assertTrue(result["structured_evidence_found"])
            self.assertEqual(result["best_evidence_type"], "manual_verified_amazon_url")
            self.assertEqual(result["best_evidence_source"], "manual_sheet_override")

    def test_normalize_amazon_url_handles_malformed_inputs(self) -> None:
        for value in (None, [], {}, "", "[]", "{}", "[https://www.amazon.com", "{not a url}", "Tatcha"):
            self.assertEqual(_normalize_amazon_url(value), "")


if __name__ == "__main__":
    unittest.main()
