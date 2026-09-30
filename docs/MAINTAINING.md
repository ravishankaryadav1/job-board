# Maintaining and extending the board

## Weekly checklist

1. Back up the durable database: `python3 -m jobboard backup state/before-refresh.sqlite`.
2. Run `python3 -m jobboard refresh`, optionally adding `--ai --max-ai-calls 20`.
   A bounded first run can use `--company biogen --max-pages 1 --max-details 3`.
   A limit is a reported gap, not a complete refresh.
3. Run `python3 -m jobboard gaps`. Investigate partial collections, source failures,
   stale records and parser errors. Do not raise budgets blindly for noisy queries.
4. Manually check companies without adapters. Open the individual official job
   page and confirm its ID, Apply state, location, eligibility and dates.
5. Inspect changed/new records with `show`. Review degree fit and specialization,
   work authorization, U.S. location, duration and relocation. Compare source
   evidence with AI suggestions before approving anything.
6. Run `export` and preview with `serve`. Inspect the student view, coverage tab
   and CSV before distributing it. Keep maintainer notes/review data private.
7. Recheck company program calendars monthly, and weekly during active recruiting
   windows. Edit `cycle.researched_at` only after actually checking the source.

Return codes: `0` completed, `1` invalid setup/unhandled task error, `2` partial
collection or incomplete enrichment, `130` interrupted. A partial refresh retains
successful checks and reports gaps. Review them; exporting afterward is allowed.

## Human corrections

Use `show` to inspect the record and evidence:

```sh
python3 -m jobboard show 'nvidia:JR2025406'
```

For a correction, create a small local JSON file, for example `state/correction.json`:

```json
{
  "track": "Discovery",
  "term_bucket": "Unknown",
  "source_notes": "The source gives a minimum of 12 weeks, not a maximum."
}
```

Then approve the current source version with your overrides:

```sh
python3 -m jobboard review 'nvidia:JR2025406' --approve --fields state/correction.json --notes "Reviewed with program coordinator"
```

`--use-ai` accepts current suggestions first, then `--fields` takes precedence.
Omit `--use-ai` to retain previously curated values. Overrides are stored in SQLite
and audited; do not edit the generated CSV as if it were the source database.
Text fields accept strings or null; classification choices are defined in
`parser.FIELD_TYPES`. API-derived availability is separate from reviewer approval.

## Manual sources and new postings

Prepare a JSON record using an actual official URL and check date. The example is
synthetic; replace all placeholder values before importing:

```json
{
  "company_id": "amgen",
  "job_id": "REPLACE_WITH_REAL_ID",
  "title": "REPLACE_WITH_OFFICIAL_TITLE",
  "url": "https://careers.amgen.com/REPLACE_WITH_JOB_PATH",
  "location": "Thousand Oaks, CA",
  "country": "United States of America",
  "status": "open",
  "checked_on": "2026-09-30",
  "evidence": "Description and working Apply control checked on the official detail page.",
  "fields": {
    "role_type": "Internship",
    "track": "Development",
    "term_bucket": "Unknown",
    "relocation": "Not stated"
  }
}
```

```sh
python3 -m jobboard record state/manual-record.json
python3 -m jobboard show 'amgen:REPLACE_WITH_REAL_ID'
python3 -m jobboard review 'amgen:REPLACE_WITH_REAL_ID' --approve
```

Manual records are not independently verified by the software. They require an
observation statement and remain pending until the separate review command. A
manual `needs_review` observation does not advance `last_verified`. For an existing
job, use the same ID, include corrected field values, and avoid older check dates.

## Add or tune company sources

Each company in `config/companies.json` has a stable lowercase `id`, display name,
search priority, hub targets, recruiting-cycle notes/sources and a `collector`.
Set `adapter: "manual"` when no tested collector exists; gaps stay visible.

For a confirmed Workday career site, configure:

```json
{
  "adapter": "workday",
  "base_url": "https://EMPLOYER.wd1.myworkdayjobs.com",
  "tenant": "EMPLOYER",
  "site": "CONFIRMED_CAREER_SITE",
  "queries": ["Intern", "Co-op", "Research Associate"],
  "include_title_patterns": ["\\b(intern(ship)?|co[\\s-]?op|research associate)\\b"]
}
```

The placeholder host is not operational. Determine the real tenant/site from the
official careers page and verify that public retrieval is permitted. Regex rules
are title filters, not skill/experience checks; a narrow rule can miss relevant
jobs. An empty/omitted list accepts every search result as a candidate. Do not add
“senior” to blanket exclusions: some senior research-associate titles accept an MS
with zero industry years.

For another ATS, add a collector returning `Collection`: normalized snapshots,
errors keyed by known requisition ID, explicit gaps and discovery/query counts.
Extend the adapter allowlist in `common.load_companies`, dispatch in `pipeline`,
and automated-coverage checks in `report`/the browser. Test against synthetic
fixtures first, then a small official-site smoke check. Keep source-specific
parsing out of `store.py` and `parser.py`.

## Later scheduling and repository setup

No scheduler is enabled. After selecting a maintainer and a durable runner, a
weekly job should back up state, run `refresh`, inspect return code 2 separately,
run `export`, and hand the changes to a reviewer. Use the same persistent database
every week. Reinitializing only from the seed on ephemeral CI would lose approvals,
history and the API cache. A monthly database retention/archive policy should be
chosen as volume grows; nothing currently auto-deletes history.

Store the API key in the runner's secret manager, not workflow YAML. Keep request
budgets explicit and monitor the API project's usage limits. Avoid overlapping
refresh runs. Poll more often only when short employer deadlines justify it.

The included test workflow uses manual dispatch with a GitHub-hosted Linux runner
and installs Python 3.11. In the repository's Actions tab, select "Test job board
(manual)" and "Run workflow" to run the offline tests. It has read-only repository
permissions and needs no API key. It does not refresh or publish the board.

Use `git@github.com:ravishankaryadav1/job-board.git` as the SSH Git remote and verify
the remote state before pushing. If SSH reports `Permission denied (publickey)`,
make sure the intended existing SSH key is registered with your GitHub account
and that the account has access to this repository. HTTPS cloning is also available
at `https://github.com/ravishankaryadav1/job-board.git`. The local `.env`, `state/`
and `build/` remain ignored; never commit your API key or maintainer database.

On the Mac used to prepare this project, `/usr/bin/git` displayed an unaccepted
Xcode license notice. The separately installed
`/Library/Developer/CommandLineTools/usr/bin/git` worked and was used to initialize
the local repository. If the default command shows that notice, use the installed
Command Line Tools Git or complete Xcode setup yourself; no license was accepted
by this project.
