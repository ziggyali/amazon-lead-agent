import unittest

from amazon_lead_agent.prompts import load_prompt


class PromptTests(unittest.TestCase):
    def test_prompt_files_load(self) -> None:
        for filename in ("extract_brand.md", "score_lead.md", "write_outreach.md"):
            content = load_prompt(filename)
            self.assertTrue(content.strip())


if __name__ == "__main__":
    unittest.main()

