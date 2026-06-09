import unittest

from amazon_lead_agent.lead_filters import is_blocked_domain, is_junk_company_name, is_likely_brand_domain, is_soft_brand_candidate


class LeadFilterTests(unittest.TestCase):
    def test_available_like_company_names_are_rejected(self) -> None:
        self.assertTrue(is_junk_company_name("AVAILABLE"))
        self.assertTrue(is_junk_company_name("AVAILABLE Definition & Meaning"))
        self.assertTrue(is_junk_company_name("Available synonym"))

    def test_blocked_root_and_subdomain_domains_are_rejected(self) -> None:
        self.assertTrue(is_blocked_domain("https://www.popsugar.com/article"))
        self.assertTrue(is_blocked_domain("https://www.sub.popsugar.com/article"))
        self.assertTrue(is_blocked_domain("https://www.amazon.com/gp/product/B000"))

    def test_likely_brand_domain_accepts_official_site_hint(self) -> None:
        self.assertTrue(is_likely_brand_domain("https://brand.example.com/pages/where-to-buy", "Brand Example", "official site", "beauty"))

    def test_soft_brand_candidate_accepts_brand_about_page(self) -> None:
        self.assertTrue(is_soft_brand_candidate("https://brand.example.com/about", "Brand Example", "our story", "beauty"))

    def test_soft_brand_candidate_accepts_brand_like_homepage_signal(self) -> None:
        self.assertTrue(is_soft_brand_candidate("https://brand.example.co.uk", "Brand Example", "skincare products", "beauty"))

    def test_likely_brand_domain_rejects_dictionary_and_listicle_results(self) -> None:
        self.assertFalse(is_likely_brand_domain("https://dictionary.com/word", "Brand Example", "official site", "beauty"))
        self.assertFalse(is_likely_brand_domain("https://popsugar.com/article", "Best Amazon Brands", "listicle", "beauty"))
        self.assertFalse(is_likely_brand_domain("https://www.imdb.com/title/tt123", "Brand Example", "movie", "beauty"))


if __name__ == "__main__":
    unittest.main()
