import os
import unittest
from unittest.mock import patch

from amazon_lead_agent.llm.gemini_client import GeminiClient
from amazon_lead_agent.llm.minimax_client import MiniMaxClient
from amazon_lead_agent.llm.router import LLMRouter


class FakeGeminiResponse:
    def __init__(self, text: str, parsed=None):
        self.text = text
        self.parsed = parsed


class FakeGeminiModels:
    def __init__(self, response: FakeGeminiResponse):
        self.response = response
        self.last_request = None

    def generate_content(self, model, contents, config):
        self.last_request = {"model": model, "contents": contents, "config": config}
        return self.response


class FakeGeminiClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.models = FakeGeminiModels(FakeGeminiResponse("OK"))


class RouterTests(unittest.TestCase):
    def test_router_selects_configured_provider(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "gem-key", "MINIMAX_API_KEY": "mini-key"}, clear=False):
            with patch("amazon_lead_agent.llm.gemini_client.genai", object()):
                with patch.object(GeminiClient, "available", return_value=True), patch.object(GeminiClient, "generate_text", return_value="OK") as mock_gemini, patch.object(MiniMaxClient, "generate_text", return_value="MINIMAX") as mock_minimax:
                    router = LLMRouter(config={"llm": {"provider": "minimax"}}, fallback_providers="minimax,gemini")
                    text = router.generate_text("Reply with exactly OK.")

        self.assertEqual(text, "OK")
        self.assertEqual(router.last_used_provider, "gemini")
        mock_gemini.assert_called_once()
        mock_minimax.assert_not_called()

    def test_missing_gemini_key_skips_to_minimax(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "", "MINIMAX_API_KEY": "mini-key"}, clear=False):
            with patch.object(MiniMaxClient, "generate_text", return_value="MINIMAX") as mock_minimax:
                router = LLMRouter(provider="gemini", fallback_providers="minimax")
                text = router.generate_text("Reply with exactly OK.")

        self.assertEqual(text, "MINIMAX")
        self.assertEqual(router.last_used_provider, "minimax")
        mock_minimax.assert_called_once()


class GeminiJsonParsingTests(unittest.TestCase):
    def test_gemini_json_extraction_handles_fenced_json(self):
        response = FakeGeminiResponse("Here is the result:\n```json\n{\"brand_name\":\"Acme\",\"website\":\"https://example.com\"}\n```")
        fake_models = FakeGeminiModels(response)
        fake_client = type("FakeClient", (), {"models": fake_models})

        with patch("amazon_lead_agent.llm.gemini_client.genai", type("GenAI", (), {"Client": lambda api_key, http_options=None: fake_client})):
            client = GeminiClient(api_key="test-key", model="gemini-2.5-flash")
            parsed = client.generate_json("Return JSON.")

        self.assertEqual(parsed["brand_name"], "Acme")
        self.assertEqual(client.last_used_provider, "gemini")
        self.assertEqual(client.last_used_model, "gemini-2.5-flash")


if __name__ == "__main__":
    unittest.main()
