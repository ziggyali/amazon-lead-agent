import unittest

from amazon_lead_agent.agents.scoring_agent import classify_scored_lead


class ScoringClassificationTests(unittest.TestCase):
    def test_contact_form_queue_classification(self) -> None:
        lead = {
            "score": 80,
            "tier": "B",
            "public_emails": [],
            "contact_page_url": "https://example.com/contact",
        }
        outcome = classify_scored_lead(lead, 75)
        self.assertEqual(outcome["status"], "contact_form_queue")

    def test_public_email_classification(self) -> None:
        lead = {
            "score": 80,
            "tier": "B",
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


if __name__ == "__main__":
    unittest.main()
