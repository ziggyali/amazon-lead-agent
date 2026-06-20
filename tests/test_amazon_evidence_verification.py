import unittest
from unittest.mock import patch

from amazon_lead_agent.tools.amazon_evidence_verification import verify_amazon_evidence


class AmazonEvidenceVerificationTests(unittest.TestCase):
    @patch("amazon_lead_agent.tools.amazon_evidence_verification.search_web")
    def test_canonical_brand_name_is_used_for_amazon_queries(self, mock_search_web) -> None:
        mock_search_web.return_value = []
        lead = {
            "seed_label": "Tatcha",
            "company_name": "Luxury Japanese Skincare Products",
            "brand_name": "Luxury Japanese Skincare Products",
            "website": "https://www.tatcha.com",
            "website_title": "Luxury Japanese Skincare Products",
            "category": "beauty",
        }

        result = verify_amazon_evidence(lead)

        self.assertEqual(result["canonical_brand_name"], "Tatcha")
        self.assertEqual(result["website_title"], "Luxury Japanese Skincare Products")
        self.assertEqual(mock_search_web.call_count, 6)
        queries = [call.args[0] for call in mock_search_web.call_args_list]
        self.assertTrue(all("Tatcha" in query for query in queries))
        self.assertTrue(all("Luxury Japanese Skincare Products" not in query for query in queries))

    @patch("amazon_lead_agent.tools.amazon_evidence_verification.search_web")
    def test_valid_storefront_search_result_counts_as_structured_evidence(self, mock_search_web) -> None:
        mock_search_web.return_value = [
            {
                "url": "https://www.amazon.com/stores/Tatcha/page/123",
                "title": "Tatcha Official Store",
                "snippet": "Visit the Tatcha Store on Amazon",
            }
        ]
        lead = {
            "seed_label": "Tatcha",
            "company_name": "Tatcha",
            "website": "https://www.tatcha.com",
            "category": "beauty",
        }

        result = verify_amazon_evidence(lead)

        self.assertTrue(result["structured_evidence_found"])
        self.assertEqual(result["best_evidence_type"], "amazon_storefront_search_result")
        self.assertEqual(result["best_evidence_confidence"], "high")
        self.assertTrue(result["amazon_backlink_found"])

    @patch("amazon_lead_agent.tools.amazon_evidence_verification.search_web")
    def test_valid_product_search_result_counts_as_medium_confidence(self, mock_search_web) -> None:
        mock_search_web.return_value = [
            {
                "url": "https://www.amazon.com/dp/B123456789",
                "title": "Tatcha Dewy Skin Cream",
                "snippet": "by Tatcha",
            }
        ]
        lead = {
            "seed_label": "Tatcha",
            "company_name": "Tatcha",
            "website": "https://www.tatcha.com",
            "category": "beauty",
        }

        result = verify_amazon_evidence(lead)

        self.assertTrue(result["structured_evidence_found"])
        self.assertEqual(result["best_evidence_type"], "amazon_product_search_result")
        self.assertEqual(result["best_evidence_confidence"], "medium")

    @patch("amazon_lead_agent.tools.amazon_evidence_verification.search_web")
    def test_generic_amazon_search_url_does_not_count_as_verified_evidence(self, mock_search_web) -> None:
        mock_search_web.return_value = [
            {
                "url": "https://www.amazon.com/s?k=Tatcha",
                "title": "Amazon.com : Tatcha",
                "snippet": "Search results for Tatcha",
            }
        ]
        lead = {
            "seed_label": "Tatcha",
            "company_name": "Tatcha",
            "website": "https://www.tatcha.com",
            "category": "beauty",
        }

        result = verify_amazon_evidence(lead)

        self.assertFalse(result["structured_evidence_found"])
        self.assertTrue(result["weak_text_signal_found"])
        self.assertEqual(result["best_evidence_type"], "weak_text_signal")

    @patch("amazon_lead_agent.tools.amazon_evidence_verification.search_web")
    def test_manual_amazon_evidence_url_counts_as_verified(self, mock_search_web) -> None:
        mock_search_web.return_value = []
        lead = {
            "seed_label": "Glossier",
            "company_name": "Glossier",
            "website": "https://www.glossier.com",
            "category": "beauty",
            "manual_amazon_evidence_url": "https://www.amazon.com/stores/glossier/page/abc",
            "manual_amazon_evidence_notes": "Manually checked in Sheet",
        }

        result = verify_amazon_evidence(lead)

        self.assertTrue(result["structured_evidence_found"])
        self.assertEqual(result["best_evidence_type"], "manual_verified_amazon_url")
        self.assertEqual(result["best_evidence_confidence"], "high")
        self.assertEqual(result["amazon_evidence_items"][0]["evidence_source"], "manual_sheet_override")


if __name__ == "__main__":
    unittest.main()
