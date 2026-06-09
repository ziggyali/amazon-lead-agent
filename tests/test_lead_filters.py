import unittest

from amazon_lead_agent.lead_filters import is_blocked_domain, is_junk_company_name, is_likely_brand_domain


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


if __name__ == "__main__":
    unittest.main()
