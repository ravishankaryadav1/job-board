import unittest

from jobboard.common import BoardError
from jobboard.http import FetchError
from jobboard.sources import collect_workday, normalize
from helpers import BASE, COMPANY, FakeSource, raw


class SourcesTest(unittest.TestCase):
    def test_pagination_and_deduplication(self):
        def search(p):
            paths = range(20) if p["offset"] == 0 else range(20, 25)
            return {"total": 25, "jobPostings": [{"externalPath": f"/job/US/R{i}"} for i in paths]}
        company = {**COMPANY, "collector": {**COMPANY["collector"], "queries": ["Intern", "Biology"]}}
        client = FakeSource(search, {BASE + f"/job/US/R{i}": raw(f"R{i}") for i in range(25)})
        batch = collect_workday(company, [], client)
        self.assertEqual((len(batch.snapshots), batch.discovered, batch.queries_completed), (25, 25, 2))
        self.assertEqual(client.requests, 29)  # 4 search pages, 25 unique details.
        self.assertEqual(batch.gaps, [])

    def test_known_job_rechecked_when_missing_from_search(self):
        client = FakeSource(lambda p: {"total": 0, "jobPostings": []}, {BASE + "/job/US/R1": raw()})
        result = collect_workday(COMPANY, [{"job_id": "R1", "source_url": BASE + "/job/US/R1"}], client)
        self.assertEqual(result.snapshots[0]["status"], "open")

    def test_404_is_failure_not_closure(self):
        client = FakeSource(lambda p: {"total": 0, "jobPostings": []},
                            {BASE + "/job/US/R1": FetchError("HTTP 404", 404)})
        result = collect_workday(COMPANY, [{"job_id": "R1", "source_url": BASE + "/job/US/R1"}], client)
        self.assertIn("R1", result.errors)
        self.assertEqual(result.snapshots, [])

    def test_limits_report_missing_coverage(self):
        client = FakeSource(lambda p: {"total": 50, "jobPostings": [
            {"externalPath": "/job/US/R1"}, {"externalPath": "/job/US/R2"}]}, {BASE + "/job/US/R1": raw()})
        result = collect_workday(COMPANY, [], client, max_pages=1, max_details=1)
        self.assertTrue(any("truncated" in gap for gap in result.gaps))
        self.assertTrue(any("Detail limit" in gap for gap in result.gaps))

    def test_repeated_page_is_not_infinite_loop(self):
        client = FakeSource(lambda p: {"total": 50, "jobPostings": [{"externalPath": "/job/US/R1"}]},
                            {BASE + "/job/US/R1": raw()})
        result = collect_workday(COMPANY, [], client)
        self.assertTrue(any("stalled" in gap for gap in result.gaps))
        self.assertEqual(client.requests, 3)

    def test_explicit_false_closes_and_missing_boolean_does_not(self):
        self.assertEqual(normalize(COMPANY, raw(canApply=False), BASE)["status"], "closed")
        payload = raw(); del payload["jobPostingInfo"]["canApply"]
        self.assertEqual(normalize(COMPANY, payload, BASE)["status"], "needs_review")

    def test_source_url_must_stay_on_configured_host(self):
        with self.assertRaises(BoardError):
            normalize(COMPANY, raw(externalUrl="https://untrusted.example/redirect"), BASE)

    def test_changed_requisition_does_not_replace_known_job(self):
        client = FakeSource(lambda p: {"total": 0, "jobPostings": []}, {BASE + "/job/US/R1": raw("R2")})
        result = collect_workday(COMPANY, [{"job_id": "R1", "source_url": BASE + "/job/US/R1"}], client)
        self.assertIn("R1", result.errors)
        self.assertFalse(result.snapshots)

    def test_relative_posted_text_does_not_change_normalized_snapshot(self):
        self.assertEqual(normalize(COMPANY, raw(postedOn="Today"), BASE),
                         normalize(COMPANY, raw(postedOn="Yesterday"), BASE))

    def test_title_rules_reduce_noise_but_recheck_known_exclusions(self):
        company = {**COMPANY, "collector": {**COMPANY["collector"], "include_title_patterns": [r"\bintern\b"]}}
        client = FakeSource(lambda p: {"total": 2, "jobPostings": [
            {"externalPath": "/job/US/R1", "title": "Internal Audit Director"},
            {"externalPath": "/job/US/R2", "title": "Biology Intern"}]},
            {BASE + "/job/US/R1": raw(), BASE + "/job/US/R2": raw("R2")})
        result = collect_workday(company, [{"job_id": "R1", "source_url": BASE + "/job/US/R1"}], client)
        self.assertEqual(result.excluded_by_title, 1)
        self.assertEqual(len(result.snapshots), 2)

    def test_missing_search_title_is_queued_and_reported(self):
        company = {**COMPANY, "collector": {**COMPANY["collector"], "include_title_patterns": ["Intern"]}}
        client = FakeSource(lambda p: {"total": 1, "jobPostings": [{"externalPath": "/job/US/R1"}]},
                            {BASE + "/job/US/R1": raw()})
        result = collect_workday(company, [], client)
        self.assertEqual(len(result.snapshots), 1)
        self.assertEqual(result.queries_completed, 1)
        self.assertTrue(any("Missing search title" in gap for gap in result.gaps))


if __name__ == "__main__":
    unittest.main()
