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

    def test_migrate_lead_queue_rows_marks_testbrand_and_available_as_rejected(self) -> None:
        class FakeStorage:
            def __init__(self):
                self.rows = [
                    {
                        "lead_id": "lead-1",
                        "brand_name": "TestBrand",
                        "company_name": "TestBrand",
                        "website": "https://example.com",
                        "status": "needs_enrichment",
                        "category": "beauty",
                    },
                    {
                        "lead_id": "lead-2",
                        "brand_name": "AVAILABLE",
                        "company_name": "AVAILABLE",
                        "website": "https://example.com",
                        "status": "needs_enrichment",
                        "category": "beauty",
                    },
                ]
                self.upserts = []

            def get_all_leads(self):
                return list(self.rows)

            def upsert_lead(self, lead, tab=None):
                self.upserts.append((tab, dict(lead)))
                return lead.get("lead_id", "")

            def commit(self):
                return None

        storage = FakeStorage()
        summary = migrate_lead_queue_rows(storage, dry_run=False)
        self.assertEqual(summary.junk_rows, 2)
        self.assertEqual(len(storage.upserts), 2)
        self.assertTrue(all(row[1]["status"] == "rejected" for row in storage.upserts))
        self.assertTrue(all(row[1]["send_status"] == "not_eligible" for row in storage.upserts))

    def test_migrate_lead_queue_rows_populates_missing_identity_fields(self) -> None:
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

            def read_lead_queue_rows(self, refresh=False):
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
        repaired = storage.upserts[0][1]
        self.assertEqual(repaired["canonical_brand_name"], "Glossier")
        self.assertEqual(repaired["status"], "needs_enrichment")
        self.assertTrue(repaired["lead_id"])
        self.assertTrue(repaired["website"])
        self.assertEqual(storage.commits, 1)

    def test_migrate_lead_queue_rows_updates_existing_row_in_place(self) -> None:
        class FakeStorage:
            def __init__(self):
                self.rows = [
                    {
                        "lead_id": "lead-1",
                        "brand_name": "glossier com",
                        "company_name": "glossier com",
                        "website": "",
                        "status": "",
                        "category": "beauty",
                    }
                ]
                self.replacements = []
                self.upserts = []
                self.commits = 0

            def read_lead_queue_rows(self, refresh=False):
                return list(self.rows)

            def replace_lead_row(self, lead, tab=None):
                self.replacements.append((tab, dict(lead)))
                self.rows[0] = dict(lead)
                return lead.get("lead_id", "")

            def upsert_lead(self, lead, tab=None):
                self.upserts.append((tab, dict(lead)))
                raise AssertionError("migration should update existing Lead Queue rows in place")

            def commit(self):
                self.commits += 1

        storage = FakeStorage()
        summary = migrate_lead_queue_rows(storage, dry_run=False)
        self.assertEqual(summary.rows_seen, 1)
        self.assertEqual(summary.rows_changed, 1)
        self.assertEqual(len(storage.replacements), 1)
        self.assertEqual(len(storage.upserts), 0)
        repaired = storage.replacements[0][1]
        self.assertEqual(storage.replacements[0][0], "Lead Queue")
        self.assertEqual(repaired["canonical_brand_name"], "Glossier")
        self.assertNotEqual(repaired["canonical_brand_name"], "Lead Queue")
        self.assertEqual(len(storage.rows), 1)
        self.assertEqual(storage.commits, 1)

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

            def read_lead_queue_rows(self, refresh=False):
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
