import unittest

from amazon_lead_agent.tools.search import clean_search_result_url


class SearchUrlTests(unittest.TestCase):
    def test_clean_duckduckgo_redirect_url(self) -> None:
        url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpath%3Fa%3Db"
        self.assertEqual(clean_search_result_url(url), "https://example.com/path?a=b")


if __name__ == "__main__":
    unittest.main()

