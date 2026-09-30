import tempfile
import unittest
from pathlib import Path

from jobboard.cli import record_manual, validate_overrides
from jobboard.common import BoardError, load_companies, plain_text
from jobboard.http import FetchError, PublicClient
from jobboard.report import job_view
from jobboard.store import Store
from helpers import COMPANY


class StubPublic(PublicClient):
    def __init__(self, robots):
        super().__init__({"demo.wd1.myworkdayjobs.com"}, delay=0)
        self.policy = robots
        self.urls = []

    def _raw(self, url, payload=None):
        self.urls.append(url)
        if url.endswith("/robots.txt"):
            if isinstance(self.policy, Exception):
                raise self.policy
            return self.policy
        return '{"ok":true}'


class HttpManualTest(unittest.TestCase):
    def test_robots_disallow_prevents_source_fetch(self):
        client = StubPublic("User-agent: *\nDisallow: /wday/")
        with self.assertRaisesRegex(FetchError, "disallows"):
            client.json("https://demo.wd1.myworkdayjobs.com/wday/cxs/jobs")
        self.assertEqual(len(client.urls), 1)

    def test_robots_missing_allows_but_access_failure_does_not(self):
        client = StubPublic(FetchError("Not found", 404))
        self.assertTrue(client.json("https://demo.wd1.myworkdayjobs.com/jobs")["ok"])
        client = StubPublic(FetchError("Forbidden", 403))
        with self.assertRaises(FetchError):
            client.json("https://demo.wd1.myworkdayjobs.com/jobs")

    def test_robots_html_is_not_a_policy(self):
        with self.assertRaises(FetchError):
            StubPublic("<!doctype html><html>Log in</html>").json("https://demo.wd1.myworkdayjobs.com/jobs")

    def test_unapproved_host_rejected_before_any_request(self):
        client = StubPublic("User-agent: *\nAllow: /")
        with self.assertRaises(BoardError):
            client.json("https://unexpected.example/jobs")
        self.assertEqual(client.urls, [])

    def test_markup_and_scripts_not_forwarded_as_html(self):
        self.assertEqual(plain_text("<p>A &amp; B</p><script>steal()</script><p>RNA</p>"), "A & B\nRNA")

    def test_manual_record_approval_and_stale_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "jobs.sqlite")
            payload = {"company_id": "demo", "job_id": "R1", "title": "Biology Intern",
                       "url": "https://example.org/jobs/R1", "location": "San Francisco",
                       "country": "United States of America", "status": "open", "checked_on": "2026-09-30",
                       "evidence": "Apply control works", "fields": {"role_type": "Internship"}}
            try:
                record_manual(store, [COMPANY], payload)
                self.assertEqual(store.get("demo:R1")["review_state"], "pending")
                with store.transaction():
                    job = store.approve("demo:R1")
                self.assertTrue(job_view(job, as_of="2026-09-30")["student_visible"])
                payload.update(status="needs_review", evidence="Page access failed")
                record_manual(store, [COMPANY], payload)
                job = store.get("demo:R1")
                self.assertEqual(job["last_verified"], "2026-09-30")
                self.assertFalse(job_view(job, as_of="2026-09-30")["student_visible"])
            finally:
                store.close()

    def test_override_enum_and_date_validation(self):
        with self.assertRaises(BoardError):
            validate_overrides({"date_to_note": "tomorrow"})
        with self.assertRaises(BoardError):
            validate_overrides({"track": "maybe"})
        with self.assertRaises(BoardError):
            validate_overrides({"status": "open"})

    def test_config_is_loadable_and_secret_free(self):
        config = Path(__file__).resolve().parents[1] / "config/companies.json"
        companies = load_companies(config)
        self.assertEqual(len(companies), 23)
        self.assertNotIn("OPENAI_API_KEY", config.read_text())


if __name__ == "__main__":
    unittest.main()
