import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amazon_lead_agent.tools.search import discover_candidates, generate_queries, get_last_search_stats


class DiscoveryStrategyTests(unittest.TestCase):
    @patch("amazon_lead_agent.tools.search._search_web_with_provider")
    def test_daily_discovery_limit_stops_query_iteration_early(self, mock_search) -> None:
        mock_search.return_value = (
            [{"title": "Brand Example", "url": "https://brand.example.com/pages/where-to-buy", "snippet": "official site"}],
            "duckduckgo",
            "ok",
        )
        with patch.dict(
            os.environ,
            {
                "DISCOVERY_MODE": "search",
                "MAX_SEARCH_QUERIES_PER_RUN": "16",
                "MAX_SEARCH_QUERIES_PER_CATEGORY": "4",
                "MAX_RESULTS_PER_QUERY": "10",
            },
            clear=False,
        ):
            results = discover_candidates(["beauty"], limit=1, config={"campaign": {"daily_discovery_limit": 1}})
        self.assertEqual(len(results), 1)
        self.assertEqual(mock_search.call_count, 1)
        self.assertEqual(get_last_search_stats()["stopped_reason"], "accepted_limit_reached")

    @patch("amazon_lead_agent.tools.search._search_web_with_provider")
    def test_max_search_queries_per_run_is_respected(self, mock_search) -> None:
        mock_search.return_value = ([], "duckduckgo", "empty")
        with patch.dict(
            os.environ,
            {
                "DISCOVERY_MODE": "search",
                "MAX_SEARCH_QUERIES_PER_RUN": "2",
                "MAX_SEARCH_QUERIES_PER_CATEGORY": "4",
                "MAX_RESULTS_PER_QUERY": "10",
            },
            clear=False,
        ):
            results = discover_candidates(["beauty"], limit=5)
        self.assertEqual(results, [])
        self.assertEqual(mock_search.call_count, 2)
        stats = get_last_search_stats()
        self.assertEqual(stats["query_budget_used"], 2)
        self.assertEqual(stats["stopped_reason"], "query_budget_exhausted")

    @patch("amazon_lead_agent.tools.search._search_web_with_provider")
    def test_max_search_queries_per_category_is_respected(self, mock_search) -> None:
        mock_search.return_value = ([], "duckduckgo", "empty")
        with patch.dict(
            os.environ,
            {
                "DISCOVERY_MODE": "search",
                "MAX_SEARCH_QUERIES_PER_RUN": "10",
                "MAX_SEARCH_QUERIES_PER_CATEGORY": "1",
                "MAX_RESULTS_PER_QUERY": "10",
            },
            clear=False,
        ):
            results = discover_candidates(["beauty"], limit=5)
        self.assertEqual(results, [])
        self.assertEqual(mock_search.call_count, 1)

    def test_category_specific_query_generation(self) -> None:
        queries = generate_queries(["beauty"])
        query_texts = [item["query"] if isinstance(item, dict) else item for item in queries]
        self.assertTrue(query_texts)
        self.assertIn('"available on Amazon" "skincare" "official"', query_texts)
        self.assertIn('inurl:where-to-buy skincare Amazon', query_texts)
        self.assertFalse(any(text.strip().lower() == "available" for text in query_texts))
        self.assertTrue(all("skincare" in text or "inurl:" in text for text in query_texts))

    @patch("amazon_lead_agent.tools.search._read_seed_sites", return_value=[{"kind": "direct", "url": "https://www.glossier.com", "label": "Glossier"}])
    def test_direct_brand_url_seed_becomes_needs_enrichment(self, mock_seed_sites) -> None:
        with patch.dict(os.environ, {"DISCOVERY_MODE": "seeded"}, clear=False):
            results = discover_candidates(["beauty"], limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://www.glossier.com")
        self.assertEqual(results[0]["category"], "beauty")
        self.assertEqual(results[0]["status_hint"], "needs_enrichment")
        self.assertEqual(results[0]["source_url"], "https://www.glossier.com")
        self.assertEqual(results[0]["source_urls"], ["https://www.glossier.com"])

    @patch("amazon_lead_agent.tools.search._read_seed_sites", return_value=[{"kind": "page", "url": "https://directory.example.com/beauty", "label": "Beauty Directory"}])
    @patch(
        "amazon_lead_agent.tools.search._fetch_public_page",
        return_value="""
        <html><body>
          <a href="https://brand.example.com">Brand Example</a>
          <a href="https://popsugar.com/article">Article</a>
        </body></html>
        """,
    )
    def test_seed_list_page_fetched_and_outbound_brand_domains_extracted(self, mock_fetch, mock_seed_sites) -> None:
        with patch.dict(os.environ, {"DISCOVERY_MODE": "seeded"}, clear=False):
            results = discover_candidates(["beauty"], limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://brand.example.com")
        self.assertEqual(results[0]["source_url"], "https://directory.example.com/beauty")
        self.assertEqual(results[0]["category"], "beauty")
        self.assertEqual(results[0].get("status_hint"), "needs_enrichment")
        self.assertEqual(results[0].get("raw_url"), "https://directory.example.com/beauty")
        self.assertEqual(results[0]["source_urls"], ["https://directory.example.com/beauty", "https://brand.example.com"])
        self.assertEqual(results[0]["brand_name"], "brand example")

    @patch("amazon_lead_agent.tools.search._read_seed_sites", return_value=[{"kind": "page", "url": "https://directory.example.com/beauty", "label": "Beauty Directory"}])
    @patch(
        "amazon_lead_agent.tools.search._fetch_public_page",
        return_value="""
        <html><body>
          <a href="https://popsugar.com/article">Article</a>
          <a href="https://brand.example.com">Brand Example</a>
          <a href="https://dictionary.com/word">Dictionary</a>
        </body></html>
        """,
    )
    def test_seed_page_itself_is_not_inserted_directly_and_blocked_outbound_domains_are_rejected(self, mock_fetch, mock_seed_sites) -> None:
        with patch.dict(os.environ, {"DISCOVERY_MODE": "seeded"}, clear=False):
            results = discover_candidates(["beauty"], limit=5)
        urls = [item["url"] for item in results]
        self.assertEqual(urls, ["https://brand.example.com"])
        self.assertNotIn("https://directory.example.com/beauty", urls)
        stats = get_last_search_stats()
        self.assertGreaterEqual(stats["seed_candidates_accepted"], 1)
        self.assertGreaterEqual(stats["seed_candidates_rejected"], 1)

    @patch("amazon_lead_agent.tools.search._read_seed_sites", return_value=[{"kind": "direct", "url": "https://www.glossier.com", "label": "Glossier"}])
    @patch("amazon_lead_agent.tools.search._search_web_with_provider")
    def test_hybrid_mode_uses_seeds_before_search(self, mock_search, mock_seed_sites) -> None:
        with patch.dict(os.environ, {"DISCOVERY_MODE": "hybrid"}, clear=False):
            results = discover_candidates(["beauty"], limit=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://www.glossier.com")
        mock_search.assert_not_called()

    @patch("amazon_lead_agent.tools.search._read_seed_sites", return_value=[{"kind": "direct", "url": "https://www.glossier.com", "label": "Glossier"}])
    @patch("amazon_lead_agent.tools.search._search_web_with_provider")
    def test_seeded_mode_can_produce_candidates_without_search(self, mock_search, mock_seed_sites) -> None:
        with patch.dict(os.environ, {"DISCOVERY_MODE": "seeded"}, clear=False):
            results = discover_candidates(["beauty"], limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://www.glossier.com")
        mock_search.assert_not_called()

    @patch("amazon_lead_agent.tools.search._search_web_with_provider")
    def test_publication_page_is_not_inserted_directly(self, mock_search) -> None:
        mock_search.return_value = (
            [
                {
                    "title": "Best Amazon Brands to Watch",
                    "url": "https://popsugar.com/article",
                    "snippet": "listicle",
                },
                {
                    "title": "Brand Example",
                    "url": "https://brand.example.com/pages/where-to-buy",
                    "snippet": "official site",
                },
            ],
            "duckduckgo",
            "ok",
        )
        with patch.dict(os.environ, {"DISCOVERY_MODE": "search"}, clear=False):
            results = discover_candidates(["beauty"], limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://brand.example.com/pages/where-to-buy")
        self.assertEqual(results[0]["category"], "beauty")

    @patch("amazon_lead_agent.tools.search._read_seed_sites", return_value=[
        {"kind": "direct", "url": "https://www.glossier.com", "label": "Glossier"},
        {"kind": "page", "url": "https://directory.example.com/beauty", "label": "Beauty Directory"},
    ])
    @patch(
        "amazon_lead_agent.tools.search._fetch_public_page",
        return_value="""
        <html><body>
          <a href="https://brand.example.com">Brand Example</a>
          <a href="https://popsugar.com/article">Article</a>
        </body></html>
        """,
    )
    def test_discovery_debug_jsonl_records_accept_and_reject_reasons(self, mock_fetch, mock_seed_sites) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with patch.dict(os.environ, {"DISCOVERY_MODE": "seeded"}, clear=False):
                    results = discover_candidates(["beauty"], limit=5)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(len(results), 2)
            debug_path = Path(tmpdir) / "logs" / "discovery_debug.jsonl"
            self.assertTrue(debug_path.exists())
            lines = [json.loads(line) for line in debug_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(any(entry["decision"] == "soft_pass" for entry in lines))
            self.assertTrue(any(entry["decision"] == "rejected" for entry in lines))
            self.assertTrue(any(entry["reason"] for entry in lines))
            self.assertTrue(any(entry.get("provider") == "seeded" for entry in lines))
            self.assertTrue(any(entry.get("seed_url") for entry in lines))

    @patch("amazon_lead_agent.tools.search._search_web_with_provider")
    def test_discovered_candidates_always_have_category(self, mock_search) -> None:
        mock_search.return_value = (
            [
                {
                    "title": "Brand Example",
                    "url": "https://brand.example.com/pages/where-to-buy",
                    "snippet": "official site",
                }
            ],
            "duckduckgo",
            "ok",
        )
        with patch.dict(os.environ, {"DISCOVERY_MODE": "search"}, clear=False):
            results = discover_candidates(["beauty"], limit=5)
        self.assertTrue(results)
        self.assertEqual(results[0]["category"], "beauty")


if __name__ == "__main__":
    unittest.main()
