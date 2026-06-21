import unittest

from amazon_lead_agent.tools.lead_queue_migration import migrate_lead_queue_rows, repair_lead_queue_row


class LeadQueueMigrationTests(unittest.TestCase):
    def test_repair_glossier_domain_row_populates_canonical_fields(self) -> None:
        repaired = repair_lead_queue_row(
            {
                "brand_name": "glossier com",
                "company_name": "glossier com",
                "website": "",
                "status": "",
                "category": "beauty",
            }
        )
        self.assertEqual(repaired["brand_name"], "Glossier")
        self.assertEqual(repaired["canonical_brand_name"], "Glossier")
        self.assertEqual(repaired["website"], "https://glossier.com")
        self.assertEqual(repaired["status"], "needs_enrichment")
        self.assertTrue(repaired["lead_id"])

    def test_migrate_lead_queue_rows_repairs_in_memory_rows(self) -> None:
        class FakeStorage:
            def __init__(self):
                self.rows = [
                    {
                        "brand_name": "glossier com",
                        "company_name": "glossier com",
                        "website": "",
                        "status": "",
                        "category": "beauty",
                    }
                ]
                self.upserts = []
                self.commits = 0

            def get_all_leads(self):
                return list(self.rows)

            def upsert_lead(self, lead, tab=None):
                self.upserts.append((tab, dict(lead)))
                return lead.get("lead_id", "")

            def commit(self):
                self.commits += 1

        storage = FakeStorage()
        summary = migrate_lead_queue_rows(storage, dry_run=False)
        self.assertEqual(summary.rows_seen, 1)
        self.assertEqual(summary.rows_changed, 1)
        self.assertEqual(len(storage.upserts), 1)
        self.assertEqual(storage.upserts[0][0], "Lead Queue")
        self.assertEqual(storage.upserts[0][1]["canonical_brand_name"], "Glossier")
        self.assertEqual(storage.commits, 1)


if __name__ == "__main__":
    unittest.main()
