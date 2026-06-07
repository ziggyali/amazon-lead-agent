import os
import tempfile
import unittest
from pathlib import Path

from amazon_lead_agent.config import load_config


class ConfigOverrideTests(unittest.TestCase):
    def test_env_overrides_win(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("storage:\n  google_sheet_id: local\ncampaign:\n  daily_draft_limit: 1\n", encoding="utf-8")
            old = os.environ.copy()
            try:
                os.environ["GOOGLE_SHEET_ID"] = "override-sheet"
                os.environ["DAILY_DRAFT_LIMIT"] = "7"
                config = load_config(config_path)
                self.assertEqual(config["storage"]["google_sheet_id"], "override-sheet")
                self.assertEqual(config["campaign"]["daily_draft_limit"], 7)
            finally:
                os.environ.clear()
                os.environ.update(old)


if __name__ == "__main__":
    unittest.main()

