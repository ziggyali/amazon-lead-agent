import unittest
from unittest.mock import Mock, patch

from amazon_lead_agent.tools.search import discover_candidates, get_last_search_stats, search_web


class SearchRouterTests(unittest.TestCase):
    def test_ddg_202_triggers_bing_fallback(self) -> None:
        ddg_response = Mock(status_code=202, text="")
        bing_response = Mock(
            status_code=200,
            text="""
            <html><body>
              <li class="b_algo">
                <h2><a href="https://brand.example.com">Brand Example</a></h2>
                <div class="b_caption"><p>Official brand site</p></div>
              </li>
            </body></html>
            """,
        )
        with patch("amazon_lead_agent.tools.search.requests.get", side_effect=[ddg_response, bing_response]):
            results = search_web("test query")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://brand.example.com")
        stats = get_last_search_stats()
        self.assertEqual(stats["blocked_query_counts"].get("duckduckgo"), 1)
        self.assertEqual(stats["provider_counts"].get("duckduckgo"), 1)
        self.assertEqual(stats["provider_counts"].get("bing_html"), 1)

    def test_listicle_domains_are_rejected(self) -> None:
        article_result = {
            "title": "Best Amazon Brands to Watch",
            "url": "https://popsugar.com/article",
            "snippet": "listicle",
        }
        brand_result = {
            "title": "Brand Example",
            "url": "https://brand.example.com/pages/where-to-buy",
            "snippet": "official site",
        }
        with patch("amazon_lead_agent.tools.search.generate_queries", return_value=["q"]), patch("amazon_lead_agent.tools.search.search_web", return_value=[article_result, brand_result]):
            results = discover_candidates(["beauty"], limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://brand.example.com/pages/where-to-buy")
        stats = get_last_search_stats()
        self.assertEqual(stats["rejected_content_domains_count"], 1)
        self.assertEqual(stats["rejected_listicle_domains_count"], 1)


if __name__ == "__main__":
    unittest.main()
