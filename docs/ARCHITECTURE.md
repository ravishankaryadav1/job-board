# Architecture and data contracts

```text
config/companies.json ──► Workday collector ──► normalized source snapshot
                                 │                       │
                         run gaps / failures       content hash
                                 │                       │
                                 └───────► SQLite ◄───────┘
                                              │
                       optional OpenAI ◄───────┤ title + description only
                         │                    │
                 evidence-backed suggestions  │
                         └──────► human review / field overrides
                                              │
                                     JSON + CSV + local board
```

## Boundaries

`common.py` validates configuration and contains shared pure helpers. `http.py`
handles public-source requests, robots checks, throttling, bounded retries and
response limits. It never receives API credentials. `sources.py` implements the
Workday adapter, including pagination and direct rechecks of existing records.

`parser.py` is the only OpenAI integration. It calls the Responses endpoint using
Python's standard HTTP library, so there is no SDK dependency to pin. To support a
different provider, implement the same `parse(title, description)` interface and
keep its output validation/review boundary. Do not mix source collection into the
model prompt or let model-generated URLs drive network requests.

`store.py` owns SQLite, stable keys and audit events. `pipeline.py` coordinates
collection and optional extraction. `report.py` is the shared projection for the
board and exports. The browser is static, dependency-free and read-only; it never
talks to OpenAI or runs collectors.

## Persistent state

All mutable state is in `state/jobs.sqlite`, ignored by Git:

| Table | Purpose |
| --- | --- |
| `jobs` | Current record, availability, curated fields, suggestions and check dates |
| `snapshots` | Compressed normalized source versions keyed by SHA-256 |
| `parse_cache` | Validated extraction, keyed by input + prompt + schema + model |
| `events` | New records, source changes, verification failures, manual checks, approvals |
| `runs` | Per-company discovery/check counts, limits, failures and completion time |

Snapshots contain the normalized description and source facts, not every raw HTML
or ATS response. This keeps the repository and database small, but means diagnosing
an upstream JSON schema change may require a new local response capture.
Identical normalized source versions occupy one snapshot; changes to relative
labels such as “posted yesterday” do not trigger new versions or API calls.

A refresh is one SQLite transaction. A crash/interruption rolls it back; source
failures handled by the pipeline commit as explicit gaps. `BEGIN IMMEDIATE` and
SQLite's writer lock prevent simultaneous writers. A second run fails after a
short lock timeout. This is a single-maintainer design, not a multi-user write API.
Back up before schema changes; future migrations must preserve these tables.

## Source, AI and human facts

- **Source facts:** employer, requisition ID, title, URL, locations, country,
  posted/end dates and explicit availability flags.
- **AI suggestions:** typed classifications and concise summaries, each with a
  quote checked against the supplied text. Suggestions do not overwrite human fields.
- **Curated fields:** accepted tags, duration, qualifications, relocation and notes.
  A new source hash sets `review_state=pending`, preserving these fields for review.
- **Calendar facts:** company-level source URLs, evidence type and research date in
  configuration. They do not silently update from an individual posting.

The dated seed has approved human summaries. For its 28 Workday records, the seed
includes hashes of the descriptions checked during the original research. A live
recheck with identical source content preserves approval. Seven manually sourced
records need manual verification until their adapters exist.

## Availability and dates

| Observation | Stored availability |
| --- | --- |
| Workday `posted=true` and `canApply=true` | `open` |
| Either flag explicitly false | `closed` |
| Missing flags, access failure, changed requisition ID, 404 | `needs_review` |
| Job absent from a search page | No inference; directly recheck its known URL |

`last_verified` advances only for an explicit open/closed observation. Errors
advance `last_checked` and retain the prior verified date. Closure never deletes
records. `first_seen` survives every refresh.

The student view requires an open, approved record checked within seven UTC
calendar days, with a U.S. country and no passed closing date or explicit
`entry_level=No`. A passed date hides a row pending recheck without overriding
employer availability. `minimum_acceptance` is not treated as a deadline.
The browser re-evaluates freshness against today's date even for an old export;
CSV/JSON visibility reflects the export's date and must be regenerated weekly.

## Known limits and extension points

Discovery is limited to configured queries and title patterns, even after full
pagination. Query totals can change during a run; results are not an atomic census.
Candidate roles can include non-U.S., senior or unrelated positions and enter the
review queue. Country is currently based on the primary Workday country field;
multi-country roles need manual review. AI is skipped for non-U.S./unknown-country
candidates to reduce unnecessary calls.

Workday public endpoints are not a promised vendor contract. Site policies, login
walls, bot controls and structural changes surface as gaps; no bypass exists.
Title exclusions are counted, known jobs remain eligible for rechecks regardless
of title rules, and request/page/detail/API budgets are explicit.

Automating the remaining 18 sources is the largest coverage gap. Add adapters with
source-specific tests for pagination, ID stability, closures and malformed data.
Do not equate HTTP 200, JobPosting JSON-LD, or an indexed page with accepting
applications. Authentication, shared hosting, email notifications and a public
write API are deliberately outside this first codebase.
