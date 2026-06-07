import unittest

from amazon_lead_agent.tools.web_extraction import extract_public_emails, filter_public_names


class WebExtractionTests(unittest.TestCase):
    def test_bad_email_filtering(self) -> None:
        html = """
        <a href="mailto:info@example.com">info@example.com</a>
        <a href="mailto:image.jpg">image.jpg</a>
        <a href="mailto:bad@theme.js">bad@theme.js</a>
        """
        emails = extract_public_emails(html, "")
        self.assertEqual(emails, ["info@example.com"])

    def test_jane_smith_rejection(self) -> None:
        names = filter_public_names(["Jane Smith", "Alex Brown", ""])
        self.assertEqual(names, ["Alex Brown"])


if __name__ == "__main__":
    unittest.main()
