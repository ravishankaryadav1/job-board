"""Coordinate adapters, durable state and optional enrichment."""

from urllib.parse import urlsplit

from .common import BoardError, now, read_json
from .http import PublicClient
from .sources import collect_workday


def seed(store, path):
    """Idempotent bootstrap. A rerun must never overwrite a maintainer's work."""
    added = 0
    with store.transaction():
        for job in read_json(path):
            key = f"{job['company_id']}:{job['job_id']}"
            if not store.get(key):
                store.save(job)
                store.event(key, "seed_import", {"snapshot_date": job["last_verified"]})
                added += 1
    return added


def enrich_one(store, job, parser):
    snapshot = store.source(job.get("source_hash"))
    if not snapshot:
        job["parser_error"] = "No fetched description; refresh this source first"
    else:
        try:
            job["suggested_fields"] = parser.parse(snapshot["title"], snapshot["description"])
            job["parser_model"] = parser.model
            job["parser_error"] = None
        except BoardError as exc:
            # Never fall back to guesses or overwrite curated fields on a failure.
            job["parser_error"] = str(exc)
    store.save(job)


def refresh(store, companies, *, parser=None, client=None, max_pages=25, max_details=100):
    automated = [c for c in companies if c["collector"]["adapter"] == "workday"]
    if not automated:
        raise BoardError("Selected companies require manual collection; see the coverage report")
    client = client or PublicClient({urlsplit(c["collector"]["base_url"]).hostname for c in automated})
    report = {"started": now(), "companies": [], "new": 0, "changed": 0, "unchanged": 0}
    with store.transaction():
        run_id = store.start_run()
        for company in automated:
            known = store.jobs(company["id"])
            batch = collect_workday(company, known, client, max_pages=max_pages, max_details=max_details)
            for snapshot in batch.snapshots:
                key = f"{snapshot['company_id']}:{snapshot['job_id']}"
                previous = store.get(key)
                job = store.ingest(snapshot)
                count = "new" if previous is None else (
                    "unchanged" if previous.get("source_hash") == job["source_hash"] else "changed")
                report[count] += 1
                if job["status"] == "needs_review":
                    batch.gaps.append(f"Availability booleans incomplete for {job['job_id']}")
                if parser and job["status"] == "open" and job.get("country") in {"United States of America", "United States", "USA"}:
                    enrich_one(store, job, parser)
            for job_id, reason in batch.errors.items():
                store.failure(f"{company['id']}:{job_id}", reason)
            report["companies"].append({
                "company_id": company["id"], "discovered": batch.discovered,
                "excluded_by_title_rules": batch.excluded_by_title,
                "checked": len(batch.snapshots), "failed_known": len(batch.errors),
                "queries_completed": batch.queries_completed,
                "queries_configured": len(company["collector"]["queries"]),
                "status": "partial" if batch.gaps or batch.errors else "complete_for_configured_queries",
                "gaps": batch.gaps,
            })
        report["requests"] = getattr(client, "requests", None)
        report["ai_calls"] = parser.calls if parser else 0
        report["ai_cache_hits"] = parser.cache_hits if parser else 0
        store.finish_run(run_id, report)
    return report
