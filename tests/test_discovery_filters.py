import unittest
from unittest.mock import patch

from amazon_lead_agent.agents.discovery_agent import run_discovery


class FakeStorage:
    def __init__(self):
        self.upserts = []
        self.events = []
        self.closed = False

    def upsert_lead(self, lead, tab=None):
        self.upserts.append((tab, lead))
        return lead.get("id", "lead-1")

    def record_outreach_event(self, event):
        self.events.append(event)

    def commit(self):
        return None

    def close(self):
        self.closed = True


class DiscoveryFilterTests(unittest.TestCase):
    @patch("amazon_lead_agent.agents.discovery_agent.discover_candidates")
    @patch("amazon_lead_agent.agents.discovery_agent.get_last_search_stats", return_value={})
    def test_available_company_is_rejected_before_insertion(self, mock_stats, mock_discover) -> None:
        mock_discover.return_value = [
            {
                "title": "AVAILABLE",
                "url": "https://brand.example.com",
                "source_url": "https://brand.example.com",
                "snippet": "",
            }
        ]
        storage = FakeStorage()
        config = {"campaign": {"categories": ["beauty"], "daily_discovery_limit": 5}}
        result = run_discovery(config, storage)
        self.assertEqual(result["leads"], [])
        self.assertEqual(storage.upserts, [])
        self.assertEqual(storage.events, [])
        self.assertTrue(storage.closed)

    @patch("amazon_lead_agent.agents.discovery_agent.discover_candidates")
    @patch("amazon_lead_agent.agents.discovery_agent.get_last_search_stats", return_value={})
    def test_available_definition_company_is_rejected_before_insertion(self, mock_stats, mock_discover) -> None:
        mock_discover.return_value = [
            {
                "title": "AVAILABLE Definition & Meaning",
                "url": "https://brand.example.com",
                "source_url": "https://brand.example.com",
                "snippet": "",
            }
        ]
        storage = FakeStorage()
        config = {"campaign": {"categories": ["beauty"], "daily_discovery_limit": 5}}
        result = run_discovery(config, storage)
        self.assertEqual(result["leads"], [])
        self.assertEqual(storage.upserts, [])
        self.assertEqual(storage.events, [])
        self.assertTrue(storage.closed)

    @patch("amazon_lead_agent.agents.discovery_agent.discover_candidates")
    @patch("amazon_lead_agent.agents.discovery_agent.get_last_search_stats", return_value={})
    def test_blocked_domain_is_rejected_before_insertion(self, mock_stats, mock_discover) -> None:
        mock_discover.return_value = [
            {
                "title": "Popsugar Article",
                "url": "https://www.sub.popsugar.com/article",
                "source_url": "https://www.sub.popsugar.com/article",
                "snippet": "news",
            }
        ]
        storage = FakeStorage()
        config = {"campaign": {"categories": ["beauty"], "daily_discovery_limit": 5}}
        result = run_discovery(config, storage)
        self.assertEqual(result["leads"], [])
        self.assertEqual(storage.upserts, [])
        self.assertEqual(storage.events, [])
        self.assertTrue(storage.closed)

    @patch("amazon_lead_agent.agents.discovery_agent.discover_candidates")
    @patch("amazon_lead_agent.agents.discovery_agent.get_last_search_stats", return_value={})
    def test_brand_like_non_junk_domain_soft_passes_to_needs_enrichment(self, mock_stats, mock_discover) -> None:
        mock_discover.return_value = [
            {
                "title": "Brand Example",
                "url": "https://store.brand.example.co.uk",
                "source_url": "https://store.brand.example.co.uk",
                "snippet": "",
                "category": "beauty",
            }
        ]
        storage = FakeStorage()
        config = {"campaign": {"categories": ["beauty"], "daily_discovery_limit": 5}}
        result = run_discovery(config, storage)
        self.assertEqual(len(result["leads"]), 1)
        self.assertEqual(result["leads"][0]["status"], "needs_enrichment")
        self.assertEqual(storage.upserts[0][1]["status"], "needs_enrichment")
        self.assertTrue(storage.closed)

    @patch("amazon_lead_agent.agents.discovery_agent.discover_candidates")
    @patch("amazon_lead_agent.agents.discovery_agent.get_last_search_stats", return_value={})
    def test_discovered_count_by_category_is_populated(self, mock_stats, mock_discover) -> None:
        mock_discover.return_value = [
            {
                "title": "Brand Example",
                "url": "https://store.brand.example.co.uk",
                "source_url": "https://store.brand.example.co.uk",
                "snippet": "",
                "category": "beauty",
            }
        ]
        storage = FakeStorage()
        config = {"campaign": {"categories": ["beauty"], "daily_discovery_limit": 5}}
        result = run_discovery(config, storage)
        self.assertEqual(result["search_stats"]["discovered_count_by_category"].get("beauty"), 1)


if __name__ == "__main__":
    unittest.main()
