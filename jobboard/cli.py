"""Small command-line interface. Run from the repository root, or pass --root."""

import argparse
import functools
import http.server
import json
import sqlite3
import sys
from pathlib import Path

from .common import BoardError, https_url, load_companies, load_env, now, read_json, today
from .parser import FIELD_TYPES, OpenAIParser
from .pipeline import enrich_one, refresh, seed
from .report import export, export_public, make_report
from .store import Store


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="UCSF AICD3 opportunity tracker")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository/config root")
    parser.add_argument("--db", type=Path, help="SQLite path (default: ROOT/state/jobs.sqlite)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Import the dated seed snapshot without overwriting existing jobs")
    p = commands.add_parser("refresh", help="Verify known jobs and discover candidates on supported portals")
    p.add_argument("--company", action="append", help="Company ID; repeat to select several")
    p.add_argument("--max-pages", type=int, default=25, help="Per search query")
    p.add_argument("--max-details", type=int, default=100, help="Per company; skipped coverage is reported")
    p.add_argument("--ai", action="store_true", help="Send public posting text to an AI provider for suggestions")
    p.add_argument("--max-ai-calls", type=int, default=20)
    p.add_argument("--provider", choices=["openai", "claude"], default="openai",
                   help="AI provider for --ai (claude requires the optional anthropic[bedrock] extra)")
    p = commands.add_parser("enrich", help="Parse stored descriptions with an AI provider; no source network calls")
    p.add_argument("--company", action="append")
    p.add_argument("--max-ai-calls", type=int, default=20)
    p.add_argument("--provider", choices=["openai", "claude"], default="openai",
                   help="AI provider (claude requires the optional anthropic[bedrock] extra)")
    p = commands.add_parser("show", help="Inspect one record and its proposed fields/evidence")
    p.add_argument("key", help="company-id:requisition-id")
    p = commands.add_parser("review", help="Explicitly approve current source facts and selected fields")
    p.add_argument("key")
    p.add_argument("--approve", action="store_true", required=True)
    p.add_argument("--use-ai", action="store_true", help="Accept all current AI suggestions after reviewing show")
    p.add_argument("--fields", type=Path, help="JSON object of field overrides; see docs/MAINTAINING.md")
    p.add_argument("--notes", help="Curator note, kept across source refreshes")
    p = commands.add_parser("record", help="Add/update a manually checked official posting from JSON")
    p.add_argument("file", type=Path)
    p = commands.add_parser("gaps", help="Print company coverage, freshness and missing-field report")
    p.add_argument("--as-of", help="YYYY-MM-DD; useful for reproducible audits")
    p = commands.add_parser("export", help="Generate board, review data and CSVs locally")
    p.add_argument("--out", type=Path)
    p.add_argument("--as-of", help="YYYY-MM-DD; defaults to current UTC date")
    p.add_argument("--public", action="store_true",
                   help="Write only student-visible jobs with no curator/review fields, to ROOT/public")
    p = commands.add_parser("serve", help="Preview generated output on localhost; no write/API-key endpoint")
    p.add_argument("--port", type=int, default=8765)
    p = commands.add_parser("backup", help="Make a consistent SQLite backup, including audit/cache")
    p.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    for name in ("max_pages", "max_details", "max_ai_calls"):
        if hasattr(args, name) and getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if getattr(args, "as_of", None):
        from datetime import date
        try:
            date.fromisoformat(args.as_of)
        except ValueError:
            parser.error("--as-of must be YYYY-MM-DD")
    return args


def validate_overrides(fields):
    allowed = set(FIELD_TYPES) | {"program_term", "hub", "source_notes", "work_mode"}
    if not isinstance(fields, dict) or not set(fields) <= allowed:
        raise BoardError("Overrides contain unknown fields; see parser.FIELD_TYPES")
    for key, value in fields.items():
        if value is not None and (not isinstance(value, str) or len(value) > 4000):
            raise BoardError("Override values must be strings or null")
        choices = FIELD_TYPES.get(key)
        if choices and value not in choices + [None]:
            raise BoardError(f"Invalid override choice for {key}")
    from .common import iso_date
    if fields.get("date_to_note") and iso_date(fields["date_to_note"]) != fields["date_to_note"]:
        raise BoardError("date_to_note must be YYYY-MM-DD or null")
    return fields


def record_manual(store, companies, payload):
    required = {"company_id", "job_id", "title", "url", "location", "country", "status", "checked_on", "evidence", "fields"}
    if not isinstance(payload, dict) or not required <= set(payload):
        raise BoardError("Manual record missing required fields; see docs/MAINTAINING.md")
    company = next((c for c in companies if c["id"] == payload["company_id"]), None)
    if company is None or payload["status"] not in {"open", "closed", "needs_review"}:
        raise BoardError("Unknown company or invalid availability")
    from .common import iso_date
    checked = iso_date(payload["checked_on"])
    if not checked or checked > today() or not payload["evidence"]:
        raise BoardError("Provide a valid non-future check date and observation evidence")
    for name in ("job_id", "title", "location", "evidence"):
        if not isinstance(payload[name], str) or not payload[name].strip():
            raise BoardError(f"Manual {name} must be a nonempty string")
    fields = validate_overrides(payload["fields"])
    url = https_url(payload["url"])
    key = f"{company['id']}:{payload['job_id']}"
    with store.transaction():
        previous = store.get(key)
        if previous and previous.get("last_verified", "")[:10] > checked:
            raise BoardError("Manual check is older than the existing verification")
        job = dict(previous or {"first_seen": checked, "source_hash": None, "curated_fields": {}, "notes": ""})
        job.update(company_id=company["id"], company=company["name"], job_id=payload["job_id"],
                   title=payload["title"], url=url, location=payload["location"], country=payload["country"], status=payload["status"],
                   review_state="pending", last_checked=checked, verification_error=None,
                   manual_evidence=payload["evidence"], suggested_fields=None, source_hash=None)
        if payload["status"] in {"open", "closed"}:
            job["last_verified"] = checked
        if not previous:
            job["source_url"] = url
        store.save(job)
        # Manual record is pending until the separate review command approves it.
        job["curated_fields"].update(fields)
        store.save(job)
        store.event(key, "manual_check", {"checked_on": checked, "evidence": payload["evidence"]})
    return {"key": key, "review_state": "pending"}


def make_ai_parser(store, provider, max_calls):
    if provider == "claude":
        # Imported lazily: anthropic[bedrock] is an optional extra, not a base
        # dependency, so `init`/`export`/`serve` never require installing it.
        from .claude_parser import ClaudeParser
        return ClaudeParser(store, max_calls=max_calls)
    return OpenAIParser(store, max_calls=max_calls)


def main(argv=None):
    args = arguments(argv)
    root = args.root.resolve()
    store = None
    try:
        load_env(root / ".env")
        companies = load_companies(root / "config/companies.json")
        selected = getattr(args, "company", None)
        if selected:
            if not set(selected) <= {c["id"] for c in companies}:
                raise BoardError("Unknown --company ID; inspect config/companies.json")
            companies_selected = [c for c in companies if c["id"] in selected]
        else:
            companies_selected = companies
        store = Store(args.db or root / "state/jobs.sqlite")
        result, exit_code = None, 0
        if args.command == "init":
            result = {"imported": seed(store, root / "data/seed_jobs.json"),
                      "snapshot": "2026-09-30 (not a live refresh)"}
        elif args.command == "refresh":
            parser = make_ai_parser(store, args.provider, args.max_ai_calls) if args.ai else None
            result = refresh(store, companies_selected, parser=parser,
                             max_pages=args.max_pages, max_details=args.max_details)
            exit_code = 2 if any(c["status"] == "partial" for c in result["companies"]) else 0
        elif args.command == "enrich":
            parser = make_ai_parser(store, args.provider, args.max_ai_calls)
            ids = {c["id"] for c in companies_selected}
            failures = 0
            with store.transaction():
                for job in store.jobs():
                    if job["company_id"] in ids and job["status"] == "open" and job.get("country") in {"United States of America", "United States", "USA"}:
                        enrich_one(store, job, parser)
                        failures += bool(job.get("parser_error"))
            result = {"ai_calls": parser.calls, "cache_hits": parser.cache_hits, "skipped_or_failed": failures}
            exit_code = 2 if failures else 0
        elif args.command == "show":
            result = store.get(args.key)
            if result is None:
                raise BoardError("Unknown job key")
        elif args.command == "review":
            fields = validate_overrides(read_json(args.fields)) if args.fields else None
            with store.transaction():
                store.approve(args.key, fields, args.use_ai, args.notes)
            result = {"approved": args.key, "next": "Run export to update the student view"}
        elif args.command == "record":
            result = record_manual(store, companies, read_json(args.file))
        elif args.command == "gaps":
            report = make_report(store, companies, as_of=args.as_of)
            result = {k: report[k] for k in ("as_of", "summary", "companies")}
        elif args.command == "export":
            if args.public:
                result = export_public(store, companies, args.out or root / "public", as_of=args.as_of)
            else:
                result = export(store, companies, args.out or root / "build", as_of=args.as_of)
        elif args.command == "backup":
            args.path.parent.mkdir(parents=True, exist_ok=True)
            store.backup(args.path)
            result = {"backup": str(args.path)}
        elif args.command == "serve":
            directory = root / "build"
            if not (directory / "index.html").exists():
                raise BoardError("Run export before serve")
            handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
            # Serve only generated output; .env and the private database are outside it.
            with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
                print(f"Local board: http://127.0.0.1:{args.port} (Ctrl+C to stop)", flush=True)
                server.serve_forever()
        if result is not None:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return exit_code
    except (BoardError, OSError, ValueError, sqlite3.Error) as exc:
        # These messages contain no HTTP bodies, keys, authorization headers or env dumps.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if store:
            store.close()
