"""A single reporting layer drives JSON, spreadsheet-friendly CSV and the board."""

import csv
import io
import json
import shutil
from collections import Counter
from pathlib import Path

from .common import age_days, iso_date, now, today, write_json

UNKNOWN = {None, "", "Unknown", "Not stated", "Uncertain", "unknown"}
REQUIRED_FIELDS = ("role_type", "track", "department", "degree", "term_bucket", "relocation")


def job_view(job, *, as_of=None):
    as_of = as_of or today()
    fields = job.get("curated_fields", {})
    view = {k: v for k, v in job.items() if k not in {"suggested_fields", "curated_fields"}}
    view.update(fields)
    # Authoritative ATS end dates take precedence over human notes about other dates.
    if job.get("deadline"):
        view["date_to_note"] = job["deadline"]
        view["date_kind"] = "closing"
    age = age_days(job.get("last_verified"), as_of)
    view["fresh"] = age is not None and 0 <= age < 7
    view["age_days"] = age
    closing = iso_date(view.get("date_to_note")) if view.get("date_kind") == "closing" else None
    # A past closing date flags uncertainty. It never silently changes ATS status.
    view["past_deadline"] = bool(closing and closing < as_of)
    view["geography_needs_review"] = job.get("country") not in {"United States of America", "United States", "USA"}
    view["student_visible"] = (job["status"] == "open" and job.get("review_state") == "approved"
                               and view["fresh"] and not view["past_deadline"]
                               and not view["geography_needs_review"] and view.get("entry_level") != "No")
    view["missing_fields"] = [name for name in REQUIRED_FIELDS if view.get(name) in UNKNOWN]
    view["ai_suggestions_available"] = bool(job.get("suggested_fields"))
    return view


def make_report(store, companies, *, as_of=None):
    as_of = as_of or today()
    jobs = [job_view(j, as_of=as_of) for j in store.jobs()]
    latest = store.latest_company_runs()
    coverage = []
    for company in companies:
        rows = [j for j in jobs if j["company_id"] == company["id"]]
        issues = []
        adapter = company["collector"]["adapter"]
        run = latest.get(company["id"])
        if adapter == "manual":
            issues.append("Automated discovery and verification not implemented")
        elif not run:
            issues.append("Collector configured but no completed run yet")
        elif run["status"] != "complete_for_configured_queries":
            issues.append("Latest collection was partial; inspect collection_gaps")
        if run and (age_days(run["finished"], as_of) or 0) >= 7:
            issues.append("Collector has not completed within seven days")
        if not rows:
            issues.append("No tracked postings; this is not evidence of no openings")
        elif not any(j["student_visible"] for j in rows):
            issues.append("No fresh, approved, open postings in the student view")
        cycle = company["cycle"]
        if (age_days(cycle["researched_at"], as_of) or 0) >= 30:
            issues.append("Recruiting calendar needs its monthly review")
        if "unknown" in cycle["evidence"].lower() or "historical" in cycle["evidence"].lower():
            issues.append("Recruiting windows have unknown or historical evidence")
        coverage.append({**company, "tracked": len(rows),
                         "student_visible": sum(j["student_visible"] for j in rows),
                         "latest_collection": run, "issues": issues})
    missing = Counter(name for j in jobs if j["status"] != "closed" for name in j["missing_fields"])
    summary = {
        "tracked_jobs": len(jobs), "student_visible": sum(j["student_visible"] for j in jobs),
        "open_at_last_check": sum(j["status"] == "open" for j in jobs),
        "review_pending": sum(j.get("review_state") != "approved" for j in jobs),
        "stale_or_unverified": sum(not j["fresh"] for j in jobs),
        "availability_needs_review": sum(j["status"] == "needs_review" for j in jobs),
        "past_deadlines": sum(j["past_deadline"] and j["status"] == "open" for j in jobs),
        "automated_companies": sum(c["collector"]["adapter"] == "workday" for c in companies),
        "total_companies": len(companies), "missing_field_counts": dict(missing),
        "parser_failures_or_skips": sum(bool(j.get("parser_error")) for j in jobs),
        "geography_needs_review": sum(j["geography_needs_review"] for j in jobs),
    }
    return {"generated_at": now(), "as_of": as_of, "summary": summary,
            "jobs": jobs, "companies": coverage,
            "scope": "Curated coverage plus configured queries; never a complete employer census."}


CSV_FIELDS = ["company", "job_id", "title", "url", "location", "role_type", "track", "term_bucket",
              "department", "degree", "eligibility", "duration_text", "program_term", "work_mode",
              "relocation", "relocation_detail", "entry_level", "bio_relevance", "skills",
              "date_to_note", "date_kind", "posted_at", "status", "review_state", "last_verified",
              "first_seen", "fresh", "student_visible", "missing_fields", "source_notes", "notes", "source_url"]


def csv_text(jobs):
    target = io.StringIO(newline="")
    writer = csv.DictWriter(target, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for job in jobs:
        row = {}
        for key in CSV_FIELDS:
            value = job.get(key, "")
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            # Employer text is untrusted. Prevent spreadsheet formula injection.
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            row[key] = value
        writer.writerow(row)
    return target.getvalue()


def export(store, companies, destination, *, as_of=None):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    report = make_report(store, companies, as_of=as_of)
    write_json(destination / "board.json", report)
    write_json(destination / "gaps.json", {k: report[k] for k in ("as_of", "summary", "companies")})
    for filename, rows in (("jobs.csv", report["jobs"]),
                           ("student_jobs.csv", [j for j in report["jobs"] if j["student_visible"]])):
        temporary = destination / (filename + ".tmp")
        temporary.write_text(csv_text(rows), encoding="utf-8-sig")
        temporary.replace(destination / filename)
    for source in (Path(__file__).parent / "web").iterdir():
        if source.is_file():
            shutil.copyfile(source, destination / source.name)
    return report["summary"]
