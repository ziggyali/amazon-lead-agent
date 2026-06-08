import unittest

from amazon_lead_agent.agents.scoring_agent import score_lead


class ScoringAgentTests(unittest.TestCase):
    def test_high_quality_lead_scores_well(self) -> None:
        lead = {
            "company_name": "Acme",
            "website": "https://example.com",
            "category": "beauty",
            "amazon_backlink_found": True,
            "amazon_links": ["https://amazon.com/stores/acme"],
            "public_emails": ["hello@example.com"],
            "contact_page_url": "https://example.com/contact",
            "pain_points": ["operations"],
            "source_urls": ["https://example.com", "https://amazon.com/stores/acme"],
        }
        scored = score_lead(lead)
        self.assertGreaterEqual(scored["score"], 75)
        self.assertIn(scored["tier"], {"A", "B"})

    def test_weak_lead_rejects(self) -> None:
        scored = score_lead({"company_name": "", "website": "", "category": "other"})
        self.assertEqual(scored["tier"], "Reject")

    def test_blocked_extraction_without_verified_signals_is_capped(self) -> None:
        lead = {
            "company_name": "Acme",
            "website": "https://example.com",
            "category": "beauty",
            "extraction_method": "blocked_or_error",
            "amazon_backlink_found": False,
            "amazon_links": [],
            "public_emails": [],
            "contact_page_url": "",
            "source_urls": ["https://example.com"],
        }
        scored = score_lead(lead)
        self.assertLessEqual(scored["score"], 45)
        self.assertEqual(scored["tier"], "Reject")


if __name__ == "__main__":
    unittest.main()

