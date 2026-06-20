import unittest

from amazon_lead_agent.normalization import ensure_lead_identity, infer_brand_name_from_domain, make_deterministic_lead_id, make_lead_id, normalize_company_name, normalize_domain


class NormalizationTests(unittest.TestCase):
    def test_normalize_company_name_strips_suffixes(self) -> None:
        self.assertEqual(normalize_company_name("Acme Brands LLC"), "acme")

    def test_normalize_domain_strips_scheme_and_www(self) -> None:
        self.assertEqual(normalize_domain("https://www.example.com/path"), "example.com")

    def test_lead_id_is_stable(self) -> None:
        lead_a = make_lead_id("Acme", "https://example.com", "https://amazon.com/stores/acme")
        lead_b = make_lead_id("Acme Inc.", "example.com", "https://amazon.com/stores/acme")
        self.assertEqual(lead_a, lead_b)

    def test_infer_brand_name_from_domain(self) -> None:
        self.assertEqual(infer_brand_name_from_domain("thehonestkitchen.com"), "The Honest Kitchen")
        self.assertEqual(infer_brand_name_from_domain("glossier.com"), "Glossier")

    def test_deterministic_lead_id_uses_domain_and_category(self) -> None:
        lead_a = make_deterministic_lead_id("glossier.com", "beauty")
        lead_b = make_deterministic_lead_id("www.glossier.com", "beauty")
        lead_c = make_deterministic_lead_id("glossier.com", "pet")
        self.assertEqual(lead_a, lead_b)
        self.assertNotEqual(lead_a, lead_c)

    def test_ensure_lead_identity_populates_ids_and_clean_name(self) -> None:
        lead = ensure_lead_identity({"website": "https://www.glossier.com", "category": "beauty"})
        self.assertTrue(lead["id"])
        self.assertEqual(lead["id"], lead["lead_id"])
        self.assertEqual(lead["company_name"], "Glossier")
        self.assertEqual(lead["brand_name"], "Glossier")

    def test_seed_label_wins_over_raw_domain_name(self) -> None:
        lead = ensure_lead_identity({"website": "https://www.thehonestkitchen.com", "category": "pet", "seed_label": "The Honest Kitchen"})
        self.assertEqual(lead["company_name"], "The Honest Kitchen")
        self.assertEqual(lead["brand_name"], "The Honest Kitchen")


if __name__ == "__main__":
    unittest.main()

