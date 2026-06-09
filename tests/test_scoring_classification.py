import unittest

from amazon_lead_agent.agents.scoring_agent import classify_scored_lead


class ScoringClassificationTests(unittest.TestCase):
    def test_contact_form_queue_classification(self) -> None:
        lead = {
            "score": 80,
            "tier": "B",
            "company_name": "Brand Example",
            "website": "https://brand.example.com",
            "category": "beauty",
            "amazon_backlink_found": True,
            "amazon_links": ["https://amazon.com/stores/brand"],
            "public_emails": [],
            "contact_page_url": "https://example.com/contact",
        }
        outcome = classify_scored_lead(lead, 75)
        self.assertEqual(outcome["status"], "contact_form_queue")

    def test_public_email_classification(self) -> None:
        lead = {
            "score": 80,
            "tier": "B",
            "company_name": "Brand Example",
            "website": "https://brand.example.com",
            "category": "beauty",
            "amazon_backlink_found": True,
            "amazon_links": ["https://amazon.com/stores/brand"],
            "public_emails": ["hello@example.com"],
            "contact_page_url": "",
        }
        outcome = classify_scored_lead(lead, 75)
        self.assertEqual(outcome["status"], "approved")

    def test_blocked_or_error_never_becomes_approved(self) -> None:
        lead = {
            "score": 95,
            "tier": "A",
            "public_emails": ["hello@example.com"],
            "contact_page_url": "https://example.com/contact",
            "extraction_method": "blocked_or_error",
        }
        outcome = classify_scored_lead(lead, 75)
        self.assertEqual(outcome["status"], "rejected")
        self.assertEqual(outcome["send_status"], "not_eligible")

    def test_high_score_without_amazon_evidence_is_not_approved(self) -> None:
        lead = {
            "score": 90,
            "tier": "A",
            "company_name": "Brand Example",
            "website": "https://brand.example.com",
            "category": "beauty",
            "public_emails": ["hello@brand.example.com"],
            "contact_page_url": "https://brand.example.com/contact",
            "extraction_method": "minimax_direct_m3",
        }
        outcome = classify_scored_lead(lead, 75, allowed_categories={"beauty"})
        self.assertEqual(outcome["status"], "needs_enrichment")
        self.assertEqual(outcome["send_status"], "not_eligible")

    def test_contact_form_queue_requires_verified_amazon_evidence(self) -> None:
        lead = {
            "score": 80,
            "tier": "B",
            "company_name": "Brand Example",
            "website": "https://brand.example.com",
            "category": "beauty",
            "public_emails": [],
            "contact_page_url": "https://brand.example.com/contact",
            "extraction_method": "minimax_direct_m3",
        }
        outcome = classify_scored_lead(lead, 75, allowed_categories={"beauty"})
        self.assertNotEqual(outcome["status"], "contact_form_queue")
        self.assertEqual(outcome["send_status"], "not_eligible")


if __name__ == "__main__":
    unittest.main()
