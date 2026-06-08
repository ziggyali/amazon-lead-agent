import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import sys

from amazon_lead_agent.agents.extraction_agent import run_extraction
from amazon_lead_agent.tools.scrapegraph_runner import extract_brand_profile
from amazon_lead_agent.tools.sqlite_store import get_connection, init_db, upsert_lead


class ExtractionMethodTests(unittest.TestCase):
    @patch("amazon_lead_agent.agents.extraction_agent.extract_brand_profile")
    def test_extraction_method_recorded(self, mock_extract) -> None:
        mock_extract.return_value = {
            "company_name": "Acme",
            "brand_name": "Acme",
            "website": "https://example.com",
            "amazon_links": [],
            "amazon_evidence_summary": "",
            "amazon_backlink_found": False,
            "public_emails": ["hello@example.com"],
            "contact_page_url": "https://example.com/contact",
            "decision_maker_source_url": "https://example.com",
            "pain_points": [],
            "confidence": 0.5,
            "source_quotes": [],
            "source_urls": ["https://example.com"],
            "extraction_method": "heuristic_fallback",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "leads.db"
            init_db(db_path)
            conn = get_connection(db_path)
            try:
                upsert_lead(conn, {"company_name": "Acme", "website": "https://example.com", "status": "discovered"})
                conn.commit()
            finally:
                conn.close()
            config = {"campaign": {"daily_discovery_limit": 10}}
            run_extraction(config, db_path)
            conn = get_connection(db_path)
            try:
                row = conn.execute("SELECT extraction_method, status FROM leads").fetchone()
                self.assertEqual(row["extraction_method"], "heuristic_fallback")
                self.assertEqual(row["status"], "enriched")
            finally:
                conn.close()

    @patch("amazon_lead_agent.tools.scrapegraph_runner._build_snapshot")
    def test_scrapegraph_success_is_labeled_other(self, mock_snapshot) -> None:
        mock_snapshot.return_value = {
            "url": "https://example.com",
            "html": "<html></html>",
            "text": "Example text",
            "links": [],
            "amazon_links": [],
            "contact_links": [],
            "public_emails": [],
            "blocked": False,
            "title": "Example Brand",
            "source_urls": ["https://example.com"],
        }

        class FakeSmartScraperGraph:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def run(self):
                return {
                    "company_name": "Example Brand",
                    "brand_name": "Example Brand",
                    "website": "https://example.com",
                    "source_urls": ["https://example.com"],
                }

        fake_module = SimpleNamespace(SmartScraperGraph=FakeSmartScraperGraph)
        with patch.dict(sys.modules, {"scrapegraphai": SimpleNamespace(graphs=fake_module), "scrapegraphai.graphs": fake_module}):
            profile = extract_brand_profile("https://example.com", minimax_api_key="test")

        self.assertEqual(profile["extraction_method"], "scrapegraphai_other")
        self.assertEqual(profile["company_name"], "Example Brand")

    @patch("amazon_lead_agent.tools.scrapegraph_runner._build_snapshot")
    def test_router_gemini_success_is_labeled_gemini_direct(self, mock_snapshot) -> None:
        mock_snapshot.return_value = {
            "url": "https://example.com",
            "html": "<html></html>",
            "text": "Example text",
            "links": [],
            "amazon_links": [],
            "contact_links": [],
            "public_emails": [],
            "blocked": False,
            "title": "Example Brand",
            "source_urls": ["https://example.com"],
        }

        class FakeRouter:
            last_used_provider = "gemini"
            last_used_model = "gemini-2.5-flash"

            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def generate_json(self, prompt, purpose="extraction"):
                return {
                    "company_name": "Example Brand",
                    "brand_name": "Example Brand",
                    "website": "https://example.com",
                    "source_urls": ["https://example.com"],
                }

        with patch("amazon_lead_agent.tools.scrapegraph_runner.LLMRouter", FakeRouter):
            profile = extract_brand_profile("https://example.com", minimax_api_key="test")

        self.assertEqual(profile["extraction_method"], "gemini_direct")
        self.assertEqual(profile["company_name"], "Example Brand")

    @patch("amazon_lead_agent.tools.scrapegraph_runner._build_snapshot")
    def test_scrapegraph_llm_failure_falls_through_to_direct_llm(self, mock_snapshot) -> None:
        mock_snapshot.return_value = {
            "url": "https://example.com",
            "html": "<html></html>",
            "text": "Example text",
            "links": [],
            "amazon_links": [],
            "contact_links": [],
            "public_emails": [],
            "blocked": False,
            "title": "Example Brand",
            "source_urls": ["https://example.com"],
        }

        class FakeSmartScraperGraph:
            def __init__(self, *args, **kwargs):
                pass

            def run(self):
                raise RuntimeError("'llm'")

        class FakeRouter:
            last_used_provider = "minimax"
            last_used_model = "MiniMax-M3"

            def __init__(self, *args, **kwargs):
                pass

            def generate_json(self, prompt, purpose="extraction"):
                return {
                    "company_name": "Example Brand",
                    "brand_name": "Example Brand",
                    "website": "https://example.com",
                    "source_urls": ["https://example.com"],
                }

        fake_module = SimpleNamespace(SmartScraperGraph=FakeSmartScraperGraph)
        with patch.dict(sys.modules, {"scrapegraphai": SimpleNamespace(graphs=fake_module), "scrapegraphai.graphs": fake_module}), patch("amazon_lead_agent.tools.scrapegraph_runner.LLMRouter", FakeRouter):
            profile = extract_brand_profile("https://example.com", minimax_api_key="test")

        self.assertEqual(profile["extraction_method"], "minimax_direct_m3")
        self.assertEqual(profile["scrapegraph_error"], "'llm'")
        self.assertEqual(profile["llm_provider_used"], "minimax")


if __name__ == "__main__":
    unittest.main()
