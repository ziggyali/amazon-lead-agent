import unittest

from amazon_lead_agent.tools.google_sheets import _sanitize_payload, _serialize_sheet_value


class GoogleSheetsSerializationTests(unittest.TestCase):
    def test_long_url_list_and_dict_are_safely_serialized(self) -> None:
        long_url = "https://example.com/" + ("a" * 3000) + "?q=1"
        value = _serialize_sheet_value("website", long_url)
        self.assertIsInstance(value, str)
        self.assertLessEqual(len(value), 1000)

        payload = _sanitize_payload(
            {
                "notes": "hello\x00world &amp; more",
                "links": ["https://example.com", {"nested": "value"}],
                "metadata": {"a": 1, "b": "two"},
            }
        )
        self.assertEqual(payload["notes"], "helloworld & more")
        self.assertIsInstance(payload["links"], str)
        self.assertLessEqual(len(payload["links"]), 1000)
        self.assertIn("nested", payload["links"])
        self.assertIsInstance(payload["metadata"], str)
        self.assertLessEqual(len(payload["metadata"]), 1000)


if __name__ == "__main__":
    unittest.main()
