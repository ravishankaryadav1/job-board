# UCSF AICD3 job board

A small, editable codebase for collecting pharma/biotech opportunities, extracting
structured details with an optional OpenAI API key, reviewing changes, and showing
students a searchable board. It also exports CSVs for Excel or Google Sheets.

**Start here:** Python 3.11+, no runtime packages to install. The included seed is
the **September 30, 2026** research snapshot: 35 postings and 23 company groups.
It is dated reference data, not a live check when you run the project later.

## Run locally

From this repository's root:

```sh
python3 -m jobboard init
python3 -m jobboard export
python3 -m jobboard serve
```

Open <http://127.0.0.1:8765>. The initial import is idempotent. It never overwrites
existing records or curator notes. If the seed is more than seven days old, use
“Include review queue and older records” to inspect it, then refresh the sources.
The default student view hides stale, unapproved, closed, non-U.S. and explicitly
non-entry-level rows. Mixed-level roles still require a curator's judgment.

Optional virtual environment: `python3 -m venv .venv`, then activate it using your
shell's usual activation command. Installing with `pip install -e .` adds a
`jobboard` command, but is unnecessary for the commands above. No Node build step,
cloud database, spreadsheet SDK or paid API is needed for the core workflow.

## Enable OpenAI parsing

1. Copy `.env.example` to `.env` locally.
2. Set `OPENAI_API_KEY` to your project key and `OPENAI_MODEL` to a model available
   to that project with Structured Outputs support. The example model is editable.
3. Run a bounded first pass:

```sh
python3 -m jobboard refresh --company gilead --ai --max-ai-calls 5
python3 -m jobboard gaps
```

Do not paste keys into chat, source files, the browser, a spreadsheet, or Git.
`.env`, the private database and generated output are ignored by Git. Only the
public job title and description go to OpenAI. The model receives no tools and
cannot browse, apply, mark jobs closed, or publish them. Requests use `store:false`.
This is not a statement about your organization's API data-retention agreement.

Responses use a strict schema and supporting source quotes. Local validation
checks field names, enum values, dates and quote presence. **Quotes are evidence
to review, not proof the model interpreted them correctly.** Extraction remains
a suggestion until a maintainer approves it. See the
[official Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

Unchanged input reuses a SQLite cache keyed by content, prompt, schema and model.
`--max-ai-calls` caps attempted calls, including failures. Oversized descriptions,
incomplete responses, refusals and exhausted budgets are reported rather than
silently truncated or guessed. There are no automatic retries of paid requests.
Tested API contracts use fake responses; a real paid call requires your local key.

You can collect first and enrich stored descriptions later:

```sh
python3 -m jobboard refresh --company nvidia
python3 -m jobboard enrich --company nvidia --max-ai-calls 5
```

## Enable Claude parsing instead

`--ai`/`enrich` accept `--provider claude` as an alternative to OpenAI, calling
Claude via AWS Bedrock (e.g. through an internal gateway such as Versa). It shares
the same prompt, schema and `validate_fields()` quote-checking as the OpenAI path
in `jobboard/parser.py` — see `jobboard/claude_parser.py`.

1. Install the optional extra: `pip install -e ".[claude]"` (adds `anthropic` and
   `boto3`; every other command stays dependency-free).
2. Set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`,
   `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` and `CLAUDE_MODEL_ID` in `.env` (standard
   boto3 names, so they match anything else in your environment already reading
   them).
3. Run with `--provider claude`:

```sh
python3 -m jobboard refresh --company gilead --ai --provider claude --max-ai-calls 5
python3 -m jobboard enrich --company nvidia --provider claude --max-ai-calls 5
```

This Bedrock deployment's Anthropic API contract predates structured outputs and
strict tool use (both return 400 "Extra inputs are not permitted"), so
`ClaudeParser` uses plain forced tool use instead; `validate_fields()` is the
actual schema/evidence guard in both providers, not the API-level schema hint.

## What to edit

| Change | File / command |
| --- | --- |
| Employers, source endpoints, search terms and recruiting cycles | `config/companies.json` |
| Extraction fields, definitions and model instructions (shared by both providers) | `jobboard/parser.py` |
| Claude/Bedrock-specific request shape | `jobboard/claude_parser.py` |
| Source-specific collection behavior | `jobboard/sources.py` |
| Student visibility and gap rules | `jobboard/report.py` |
| Layout and browser filters | `jobboard/web/` |
| Human corrections and approvals | `show`, `record`, and `review` commands |

Important comments explain decisions around closure, deadlines, evidence,
pagination and preservation of human edits. Files are organized by responsibility,
with no application framework or generated dependency tree.

## Coverage and gaps

- **Five automated Workday sources:** Genentech, Gilead, NVIDIA, Moderna and Biogen.
- **18 manual sources** are explicitly identified. They include employers with
  existing seed jobs and employers researched only at the program level. They do
  not falsely appear as successfully scraped.
- Search terms and optional title rules define discovery coverage. Workday text
  search can match “internal” when searching “intern”; title rules reduce that
  noise. Excluded counts, partial pagination and detail limits are reported.
- Known postings are rechecked by ID/detail URL even if absent from search results.
  A disappearance, 404, redirect, timeout or CAPTCHA is **not** proof of closure.
- Recruiting calendars are separately sourced and manually maintained. A live job
  does not establish the company's next annual opening or expiration window.
- The company register adds Johnson & Johnson, which was present in the first
  spreadsheet's job rows but missing from its company tab.

Run `python3 -m jobboard gaps` or open the coverage tab. Initial seed gaps include
four unknown duration buckets and 16 unspecified relocation categories. Unknown
relocation means the source did not specify it, not that assistance is unavailable.

## Review and export

```sh
python3 -m jobboard show 'nvidia:JR2025406'
# After reading the posting and proposed fields:
python3 -m jobboard review 'nvidia:JR2025406' --approve --use-ai
python3 -m jobboard export
```

`--use-ai` requires current suggestions; omit it to retain existing curated fields.
New source versions invalidate approval, while preserving previous human fields
and notes for comparison. `[company-id]:[requisition-id]` is the stable record key.

Generated output in `build/`:

- `index.html`, `app.js`, `styles.css`: local browser board.
- `board.json`: all records and coverage data for a future frontend/integration.
- `jobs.csv`: all record states for maintainers; `student_jobs.csv`: filtered view.
- `gaps.json`: company-level gaps, calendar evidence and data-quality counts.

`build/` includes curator notes and review records; treat it as a maintainer preview.
Never point public hosting at `build/`. Access control, Google Sheets sync and
notifications are not implemented in this version.

## Publish a public site

`python3 -m jobboard export --public` writes to `public/` instead of `build/`, using
only student-visible jobs (open, approved, fresh, U.S., not explicitly non-entry-level)
and dropping curator notes, review state history, manual evidence and every other
maintainer-only field. Only what the browser board already displays for a visible
row leaves the machine. Diff `public/board.json` before pushing if you are unsure
what changed.

`public/` is committed to Git (unlike `build/`, `state/`, or `.env`), so a redeploy is:

```sh
python3 -m jobboard refresh --ai --max-ai-calls 20   # optional: refresh sources first
python3 -m jobboard export --public
git add public
git commit -m "Refresh public board"
git push
```

To host it on Vercel, `vercel.json` at the repo root pins `outputDirectory` to
`public` with no build step, since the site is already static files. There is no
scheduled refresh: republishing is a deliberate, manual step so no OpenAI/Claude
key or source-fetching credentials ever need to live in a CI secret.

### Deploying to Vercel after a push

`vercel link` connects a local checkout to the Vercel project once (`.vercel/` is
git-ignored; re-run `vercel link` on a fresh clone). If the Vercel project isn't
Git-connected (e.g. you only have collaborator access to the GitHub repo, not
owner/admin access needed to install Vercel's GitHub App), `git push` alone does
**not** redeploy — Vercel has no way to know the push happened. Treat every commit
+ push that touches `public/` as also requiring this sequence:

```sh
vercel deploy                                   # preview build
vercel curl <preview-url>/board.json -- -s -o /tmp/preview_board.json
python3 -c "import json; print(json.load(open('/tmp/preview_board.json'))['summary'])"
vercel deploy --prod                            # promote once the preview checks out
```

Preview URLs are SSO-protected, so use `vercel curl` (not a plain `curl`/fetch) to
inspect one. Don't skip the preview-then-promote split just to save a step; it's
what catches a bad export before it reaches the production alias.

## Weekly operation and development

See [Maintaining the board](docs/MAINTAINING.md) for the weekly checklist, manual
records, overrides, extending collectors and preparing scheduled runs. See
[Architecture](docs/ARCHITECTURE.md) for data flow and state semantics.

```sh
python3 -m unittest discover -s tests -v
```

Tests are offline and use synthetic postings and API responses. The optional
GitHub Actions workflow is **manual-only**, uses a GitHub-hosted Linux runner
with Python 3.11, and makes no source/API calls. There is no active schedule.

## GitHub repository

Repository: <https://github.com/ravishankaryadav1/job-board>.
Clone over HTTPS:

```sh
git clone https://github.com/ravishankaryadav1/job-board.git
cd job-board
```

For SSH access, register your public key with your GitHub account and use
`git@github.com:ravishankaryadav1/job-board.git`.

If starting from the ZIP, initialize a local repository with `git init -b main`
and set its remote with
`git remote add origin https://github.com/ravishankaryadav1/job-board.git`.
Inspect the remote before an initial push; fetch and integrate any existing work.
For an empty remote, the initial commit/push is:

```sh
git status
git add .
git commit -m "Add configurable AICD3 job board pipeline"
git push -u origin main
```

Review staged files first and never force-push over existing work. No open-source
license has been chosen on behalf of UCSF; decide that before public redistribution.
