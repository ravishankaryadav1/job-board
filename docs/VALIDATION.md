# Validation — September 30, 2026

- 40 offline regression tests pass using Python's built-in `unittest` runner.
- Python compilation and browser JavaScript syntax checks pass.
- Seed import/export verified: 35 distinct records, 23 registered company groups,
  five automated collectors. Reimport does not overwrite records.
- Live, read-only smoke checks retrieved and normalized one posting from each of
  Genentech, Gilead, NVIDIA, Biogen and Moderna. These deliberately small runs
  reported page/detail limits as partial coverage; they were not full refreshes.
- A missing title in one Biogen search result exposed a schema edge case. The
  collector now reports it, queues the detail page, and continues pagination.
  A synthetic regression test covers that behavior.
- Browser checks confirmed 35 initial rows, 23 Bay Area options, two NVIDIA
  matches, one NVIDIA job when filtered by role type, 23 company cards, and
  expanded eligibility/relocation/duration notes.
- Secret/state/output ignore rules and the intended GitHub repository were checked.

The tests cover pagination and repeated-page detection, title filters, deduplication,
rechecking jobs absent from search, source failures without false closure,
transaction rollback, preserving human notes, stale dates, minimum acceptance vs
closing dates, U.S. scope, API schema/evidence validation, cache reuse/invalidation,
call limits, refusal/incomplete output, manual approvals, CSV injection protection,
robots-policy handling and backups.

No paid OpenAI request was made. Provider integration tests use simulated responses;
live account access, selected-model behavior and extraction quality on real postings
must be checked after a maintainer supplies a local API key. The GitHub workflow
was not run. Public deployment and recurring scheduling are not part of these checks.
