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


if __name__ == "__main__":
    unittest.main()
