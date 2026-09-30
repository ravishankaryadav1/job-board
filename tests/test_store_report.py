import tempfile
import unittest
from pathlib import Path

from jobboard.pipeline import refresh, seed
from jobboard.report import csv_text, export, export_public, job_view, make_report
from jobboard.sources import normalize
from jobboard.store import Store
from helpers import BASE, COMPANY, FakeSource, raw


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "jobs.sqlite")
        self.snapshot = normalize(COMPANY, raw(), BASE + "/job/US/R1")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_dedupe_and_notes_survive_change(self):
        with self.store.transaction():
            self.store.ingest(self.snapshot, "2026-09-20")
            self.store.approve("demo:R1", {"track": "Discovery"}, notes="Ask career office")
            self.store.ingest({**self.snapshot, "title": "Updated Intern"}, "2026-09-30")
        jobs = self.store.jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["notes"], "Ask career office")
        self.assertEqual(jobs[0]["curated_fields"]["track"], "Discovery")
        self.assertEqual(jobs[0]["first_seen"], "2026-09-20")
        self.assertEqual(jobs[0]["review_state"], "pending")

    def test_unchanged_source_preserves_approval(self):
        with self.store.transaction():
            self.store.ingest(self.snapshot, "2026-09-20")
            self.store.approve("demo:R1")
            self.store.ingest(self.snapshot, "2026-09-30")
        self.assertEqual(self.store.get("demo:R1")["review_state"], "approved")

    def test_changing_time_left_to_apply_preserves_approval(self):
        with self.store.transaction():
            self.store.ingest({**self.snapshot, "time_left_to_apply": "3 days left to apply"}, "2026-09-20")
            self.store.approve("demo:R1")
            self.store.ingest({**self.snapshot, "time_left_to_apply": "7 hours left to apply"}, "2026-09-30")
        job = self.store.get("demo:R1")
        self.assertEqual(job["review_state"], "approved")
        self.assertEqual(job["time_left_to_apply"], "7 hours left to apply")

    def test_failed_fetch_keeps_last_verified(self):
        with self.store.transaction():
            self.store.ingest(self.snapshot, "2026-09-20")
            self.store.failure("demo:R1", "403; challenge", "2026-09-30")
        job = self.store.get("demo:R1")
        self.assertEqual(job["status"], "needs_review")
        self.assertEqual(job["last_verified"], "2026-09-20")

    def test_transaction_rolls_back_incomplete_refresh(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.ingest(self.snapshot)
                raise RuntimeError("interrupted")
        self.assertFalse(self.store.jobs())

    def test_minimum_acceptance_is_not_expiry(self):
        with self.store.transaction():
            job = self.store.ingest(self.snapshot, "2026-09-30")
            job = self.store.approve("demo:R1", {"date_to_note": "2026-09-20", "date_kind": "minimum_acceptance"})
        self.assertTrue(job_view(job, as_of="2026-09-30")["student_visible"])
        job["curated_fields"]["date_kind"] = "closing"
        self.assertFalse(job_view(job, as_of="2026-09-30")["student_visible"])
        self.assertEqual(job["status"], "open")

    def test_staleness_at_seven_days(self):
        with self.store.transaction():
            self.store.ingest(self.snapshot, "2026-09-23")
            job = self.store.approve("demo:R1")
        self.assertFalse(job_view(job, as_of="2026-09-30")["student_visible"])
        self.assertTrue(job_view(job, as_of="2026-09-29")["student_visible"])

    def test_non_us_and_senior_rows_not_student_visible(self):
        with self.store.transaction():
            self.store.ingest({**self.snapshot, "country": "Germany"}, "2026-09-30")
            job = self.store.approve("demo:R1")
        self.assertFalse(job_view(job, as_of="2026-09-30")["student_visible"])
        job["country"] = "United States of America"
        job["curated_fields"]["entry_level"] = "No"
        self.assertFalse(job_view(job, as_of="2026-09-30")["student_visible"])

    def test_seed_is_idempotent_and_covers_company_register(self):
        import json
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(seed(self.store, root / "data/seed_jobs.json"), 35)
        self.assertEqual(seed(self.store, root / "data/seed_jobs.json"), 0)
        companies = json.loads((root / "config/companies.json").read_text())
        self.assertTrue({j["company_id"] for j in self.store.jobs()} <= {c["id"] for c in companies})
        summary = export(self.store, companies, Path(self.temp.name) / "build", as_of="2026-09-30")
        self.assertEqual(summary["student_visible"], 35)
        self.assertEqual(summary["automated_companies"], 6)
        self.assertEqual(summary["total_companies"], 30)

    def test_public_export_hides_curator_notes_and_pending_records(self):
        import json
        with self.store.transaction():
            self.store.ingest(self.snapshot, "2026-09-30")
            self.store.approve("demo:R1", notes="Reviewed with program coordinator")
            self.store.ingest({**self.snapshot, "job_id": "R2", "title": "Pending Intern"}, "2026-09-30")
        summary = export_public(self.store, [COMPANY], Path(self.temp.name) / "public", as_of="2026-09-30")
        self.assertEqual(summary["student_visible"], 1)
        board = json.loads((Path(self.temp.name) / "public/board.json").read_text())
        self.assertEqual(len(board["jobs"]), 1)
        self.assertEqual(board["jobs"][0]["job_id"], "R1")
        for key in ("notes", "manual_evidence", "verification_error", "parser_error",
                    "curated_fields", "suggested_fields", "source_hash", "approved_hash"):
            self.assertNotIn(key, board["jobs"][0])

    def test_csv_neutralizes_formula_injection(self):
        csv = csv_text([{"company": "=DANGEROUS()", "title": " +CMD", "url": "https://example.org"}])
        self.assertIn("'=DANGEROUS()", csv)
        self.assertIn("' +CMD", csv)
        self.assertIn("https://example.org", csv)

    def test_partial_run_is_visible_in_coverage(self):
        client = FakeSource(lambda p: {"total": 10, "jobPostings": [{"externalPath": "/job/US/R1"}]},
                            {BASE + "/job/US/R1": raw()})
        refresh(self.store, [COMPANY], client=client, max_pages=1)
        report = make_report(self.store, [COMPANY], as_of="2026-09-30")
        self.assertEqual(report["companies"][0]["latest_collection"]["status"], "partial")
        self.assertEqual(report["summary"]["review_pending"], 1)

    def test_backup_is_readable_and_independent(self):
        with self.store.transaction():
            self.store.ingest(self.snapshot)
        path = Path(self.temp.name) / "backup.sqlite"
        self.store.backup(path)
        copy = Store(path)
        try:
            self.assertEqual(len(copy.jobs()), 1)
        finally:
            copy.close()


if __name__ == "__main__":
    unittest.main()
