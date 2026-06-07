import unittest

from amazon_lead_agent.normalization import make_lead_id, normalize_company_name, normalize_domain


class NormalizationTests(unittest.TestCase):
    def test_normalize_company_name_strips_suffixes(self) -> None:
        self.assertEqual(normalize_company_name("Acme Brands LLC"), "acme")

    def test_normalize_domain_strips_scheme_and_www(self) -> None:
        self.assertEqual(normalize_domain("https://www.example.com/path"), "example.com")

    def test_lead_id_is_stable(self) -> None:
        lead_a = make_lead_id("Acme", "https://example.com", "https://amazon.com/stores/acme")
        lead_b = make_lead_id("Acme Inc.", "example.com", "https://amazon.com/stores/acme")
        self.assertEqual(lead_a, lead_b)


if __name__ == "__main__":
    unittest.main()

