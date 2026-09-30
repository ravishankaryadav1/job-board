"""One SQLite file holds jobs, compressed source versions, parse cache and audit log.

Jobs use stable (company_id, requisition_id) keys. No refresh deletes a row.
An error changes availability to needs_review without advancing last_verified.
"""

import json
import sqlite3
import zlib
from contextlib import contextmanager
from pathlib import Path

from .common import BoardError, digest, now


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                key TEXT PRIMARY KEY, company_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company_id);
            CREATE TABLE IF NOT EXISTS snapshots (
                hash TEXT PRIMARY KEY, payload BLOB NOT NULL, first_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS parse_cache (
                key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, at TEXT NOT NULL, job_key TEXT NOT NULL,
                kind TEXT NOT NULL, details TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, started TEXT NOT NULL, finished TEXT, data TEXT);
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        # Serializes writers before collecting. An interrupted run rolls back jobs,
        # approvals and run state together; a simultaneous writer fails safely.
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def jobs(self, company_id=None):
        query, args = ("SELECT data FROM jobs ORDER BY key", ()) if company_id is None else (
            "SELECT data FROM jobs WHERE company_id=? ORDER BY key", (company_id,))
        return [json.loads(row[0]) for row in self.db.execute(query, args)]

    def get(self, key):
        row = self.db.execute("SELECT data FROM jobs WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, job):
        key = f"{job['company_id']}:{job['job_id']}"
        job["key"] = key
        self.db.execute("INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                        (key, job["company_id"], json.dumps(job, ensure_ascii=False)))

    def event(self, key, kind, details):
        self.db.execute("INSERT INTO events(at,job_key,kind,details) VALUES (?,?,?,?)",
                        (now(), key, kind, json.dumps(details, ensure_ascii=False)))

    def cache_get(self, key):
        row = self.db.execute("SELECT data FROM parse_cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO parse_cache VALUES (?,?)", (key, json.dumps(value)))

    def source(self, hash_value):
        row = self.db.execute("SELECT payload FROM snapshots WHERE hash=?", (hash_value,)).fetchone()
        return json.loads(zlib.decompress(row[0])) if row else None

    def ingest(self, snapshot, checked_at=None):
        checked_at = checked_at or now()
        key = f"{snapshot['company_id']}:{snapshot['job_id']}"
        old = self.get(key)
        source_hash = digest(snapshot)
        self.db.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?,?)",
                        (source_hash, zlib.compress(json.dumps(snapshot).encode()), checked_at))
        changed = old is None or old.get("source_hash") != source_hash
        # Preserve human fields and first discovery even if the source changes.
        job = dict(old or {"first_seen": checked_at, "curated_fields": {}, "notes": "",
                          "review_state": "pending", "suggested_fields": None})
        job.update({k: v for k, v in snapshot.items() if k != "description"})
        job.update(source_hash=source_hash, last_checked=checked_at, verification_error=None)
        if snapshot["status"] in {"open", "closed"}:
            job["last_verified"] = checked_at
        if changed:
            job["review_state"] = "pending"
            job["suggested_fields"] = None
            job["parser_error"] = None
            self.event(key, "new" if old is None else "source_changed", {
                "previous_hash": (old or {}).get("source_hash"), "source_hash": source_hash,
                "previous_status": (old or {}).get("status"), "status": snapshot["status"]})
        self.save(job)
        return job

    def failure(self, key, error, checked_at=None):
        job = self.get(key)
        if job:
            job.update(status="needs_review", verification_error=error, last_checked=checked_at or now())
            self.save(job)
            self.event(key, "verification_failed", {"reason": error})

    def approve(self, key, fields=None, use_ai=False, notes=None):
        job = self.get(key)
        if job is None:
            raise BoardError("Unknown job key")
        curated = dict(job.get("curated_fields", {}))
        if use_ai:
            if not job.get("suggested_fields"):
                raise BoardError("No current AI suggestions to approve")
            curated.update({k: v["value"] for k, v in job["suggested_fields"].items()})
        curated.update(fields or {})
        job.update(curated_fields=curated, review_state="approved", approved_hash=job.get("source_hash"))
        if notes is not None:
            job["notes"] = notes
        self.save(job)
        self.event(key, "approved", {"source_hash": job.get("source_hash"), "used_ai": use_ai,
                                      "updated_fields": sorted((fields or {}).keys())})
        return job

    def start_run(self):
        return self.db.execute("INSERT INTO runs(started) VALUES (?)", (now(),)).lastrowid

    def finish_run(self, run_id, data):
        self.db.execute("UPDATE runs SET finished=?,data=? WHERE id=?", (now(), json.dumps(data), run_id))

    def latest_company_runs(self):
        results = {}
        for row in self.db.execute("SELECT finished,data FROM runs WHERE finished IS NOT NULL ORDER BY id DESC"):
            for company in json.loads(row["data"]).get("companies", []):
                results.setdefault(company["company_id"], {**company, "finished": row["finished"]})
        return results

    def backup(self, path):
        destination = sqlite3.connect(path)
        try:
            self.db.backup(destination)
        finally:
            destination.close()
