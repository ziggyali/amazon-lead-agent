import unittest

from amazon_lead_agent.tools.amazon_backlink_discovery import (
    contains_amazon_buying_signal,
    extract_amazon_links,
    has_verified_amazon_evidence,
    summarize_amazon_evidence,
)


class AmazonBacklinkTests(unittest.TestCase):
    def test_extract_amazon_links(self) -> None:
        html = '<a href="https://www.amazon.com/stores/acme">Amazon Store</a><a href="https://amazon.com/dp/B123">Buy</a><a href="/stores/acme">Not Amazon</a>'
        links = extract_amazon_links(html, "https://example.com")
        self.assertEqual(
            links,
            ["https://amazon.com/dp/B123", "https://www.amazon.com/stores/acme"],
        )

    def test_contains_signal(self) -> None:
        self.assertTrue(contains_amazon_buying_signal("Available on Amazon now"))

    def test_summary_mentions_links_and_signals(self) -> None:
        summary = summarize_amazon_evidence(["https://amazon.com/stores/acme"], "Buy on Amazon")
        self.assertIn("Amazon links:", summary)
        self.assertIn("Signals:", summary)

    def test_verified_evidence_requires_structured_url(self) -> None:
        self.assertFalse(has_verified_amazon_evidence({"amazon_evidence_summary": "available on Amazon"}))
        self.assertTrue(has_verified_amazon_evidence({"amazon_links": ["https://www.amazon.com/stores/acme"]}))


if __name__ == "__main__":
    unittest.main()

