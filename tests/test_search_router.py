import unittest
from unittest.mock import Mock, patch

from amazon_lead_agent.tools.search import clean_search_result_url, discover_candidates, get_last_search_stats, search_web


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

    def test_bing_ck_a_url_is_decoded_or_rejected(self) -> None:
        encoded_target = "a1aHR0cHM6Ly9icmFuZC5leGFtcGxlLmNvbS9wYWdl"
        bing_url = f"https://www.bing.com/ck/a?!&&p=abc123&u={encoded_target}&c=1&amp;foo=bar"
        cleaned = clean_search_result_url(bing_url)
        self.assertEqual(cleaned, "https://brand.example.com/page")
        self.assertEqual(clean_search_result_url("https://www.bing.com/ck/a?!&&p=abc123&u=badpayload&c=1"), "")

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
        self.assertEqual(stats["rejected_content_domain_count"], 1)
        self.assertEqual(stats["rejected_listicle_domains_count"], 1)

    def test_dictionary_news_marketplace_domains_are_rejected(self) -> None:
        rejected_result = {
            "title": "Brand Example",
            "url": "https://news.example.com/article",
            "snippet": "directory entry",
        }
        accepted_result = {
            "title": "Brand Example",
            "url": "https://brand.example.com/retailers",
            "snippet": "official site",
        }
        with patch("amazon_lead_agent.tools.search.generate_queries", return_value=["q"]), patch("amazon_lead_agent.tools.search.search_web", return_value=[rejected_result, accepted_result]):
            results = discover_candidates(["beauty"], limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://brand.example.com/retailers")



if __name__ == "__main__":
    unittest.main()
