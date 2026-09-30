"""Synthetic fixtures: no live requests, credentials or copied employer prose."""
from copy import deepcopy

COMPANY = {"id": "demo", "name": "Demo Biology", "collector": {
    "adapter": "workday", "base_url": "https://demo.wd1.myworkdayjobs.com",
    "tenant": "demo", "site": "Careers", "queries": ["Intern"]},
    "cycle": {"source_url": "https://example.org/careers", "researched_at": "2026-09-30",
              "evidence": "Historical; dates unknown"}}
BASE = "https://demo.wd1.myworkdayjobs.com/wday/cxs/demo/Careers"


def raw(job_id="R1", **changes):
    result = {"jobPostingInfo": {
        "jobReqId": job_id, "title": "Biology Intern", "posted": True, "canApply": True,
        "externalUrl": f"https://demo.wd1.myworkdayjobs.com/Careers/job/US/{job_id}",
        "location": "South San Francisco", "country": {"descriptor": "United States of America"},
        "jobDescription": "<p>Study protein design. This is a 12-week internship.</p>",
        "startDate": "2026-09-20"}}
    result["jobPostingInfo"].update(changes)
    return result


class FakeSource:
    def __init__(self, search, details):
        self.search = search
        self.details = details
        self.requests = 0

    def json(self, url, payload=None):
        self.requests += 1
        value = self.search(payload) if payload is not None else self.details[url]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)
