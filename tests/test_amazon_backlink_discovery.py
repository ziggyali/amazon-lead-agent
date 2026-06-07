import unittest

from amazon_lead_agent.tools.amazon_backlink_discovery import (
    contains_amazon_buying_signal,
    extract_amazon_links,
    summarize_amazon_evidence,
)


class AmazonBacklinkTests(unittest.TestCase):
    def test_extract_amazon_links(self) -> None:
        html = '<a href="/stores/acme">Amazon Store</a><a href="https://amazon.com/dp/B123">Buy</a>'
        links = extract_amazon_links(html, "https://example.com")
        self.assertEqual(
            links,
            ["https://amazon.com/dp/B123", "https://example.com/stores/acme"],
        )

    def test_contains_signal(self) -> None:
        self.assertTrue(contains_amazon_buying_signal("Available on Amazon now"))

    def test_summary_mentions_links_and_signals(self) -> None:
        summary = summarize_amazon_evidence(["https://amazon.com/stores/acme"], "Buy on Amazon")
        self.assertIn("Amazon links:", summary)
        self.assertIn("Signals:", summary)


if __name__ == "__main__":
    unittest.main()

