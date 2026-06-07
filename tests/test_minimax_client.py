import json
import unittest
from unittest.mock import patch

from amazon_lead_agent.llm.minimax_client import MiniMaxClient


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class MiniMaxClientTests(unittest.TestCase):
    @patch("amazon_lead_agent.llm.minimax_client.requests.post")
    def test_chatcompletion_v2_text_block_parsing(self, mock_post):
        mock_post.return_value = _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "thinking", "text": "ignore"},
                                {"type": "text", "text": "hello"},
                            ]
                        }
                    }
                ]
            }
        )
        client = MiniMaxClient(api_key="x", api_style="chatcompletion_v2", model="MiniMax-M3", api_base="https://example.com")
        self.assertEqual(client.generate_text("hi"), "hello")

    @patch("amazon_lead_agent.llm.minimax_client.requests.post")
    def test_anthropic_json_parsing(self, mock_post):
        mock_post.return_value = _FakeResponse(
            {
                "content": [
                    {"type": "thinking", "text": "ignore"},
                    {"type": "text", "text": '{"brand_name":"Acme"}'},
                ]
            }
        )
        client = MiniMaxClient(
            api_key="x",
            api_style="anthropic_messages",
            model="MiniMax-M2.7",
            api_base="https://example.com",
        )
        parsed = client.generate_json("hi")
        self.assertEqual(parsed["brand_name"], "Acme")


if __name__ == "__main__":
    unittest.main()

