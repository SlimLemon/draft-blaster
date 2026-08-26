# Draft-Day Emergency Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the current auction co-pilot safe and fully verified for today's draft.

**Architecture:** Keep the existing workbook-owned strategy and UNO controller. Add explicit commit semantics around workbook mutation, one normalized ESPN sale queue, isolated LibreOffice test ownership, strict configuration/cache boundaries, and targeted workbook formula/style repairs.

**Tech Stack:** Python 3 / LibreOffice UNO, `unittest`, PowerShell launch workflow, OOXML `.xlsx`, `@oai/artifact-tool`, Git.

---

### Task 1: Secret-safe version-control baseline

**Files:**
- Create: `.gitignore`
- Create: `docs/superpowers/specs/2026-08-26-draft-day-emergency-remediation-design.md`
- Create: `docs/superpowers/plans/2026-08-26-draft-day-emergency-remediation.md`

- [x] Add ignore rules for `espn_config.json`, player caches, live ESPN fixtures, journals, Python/test caches, local audit folders, LibreOffice lock files, and worktrees.
- [x] Initialize Git on `main`, inspect the exact staged set, and commit only the design, plan, source, examples, static fixture, runbook, and workbook assets.
- [x] Verify `git ls-files` contains no live cookie configuration or generated cache.

### Task 2: Core sale-path regression tests and implementation

**Files:**
- Create: `test_draft_copilot.py`
- Modify: `draft_copilot.py`
- Modify: `selftest.py`

- [x] Add discoverable tests that reproduce empty UNO tuple cells, named winner parsing, ME-only max-bid checks, heartbeat rollback, queue retention, cleanup ordering, canonical document matching, and isolated test ownership.
- [x] Run `python -B -m unittest test_draft_copilot -v` and confirm the new tests fail for the reviewed reasons.
- [x] Flatten roster values with `[row[0] for row in self.arr(...)]`, return `(True, "")` on valid slots, and replace the invalid WR-to-RB help example.
- [x] Extract a unique winner fragment from non-price/non-slot tokens before player resolution.
- [x] Snapshot `B/D/E/F/I`, restore on write/recalc/heartbeat failure, and make `do_sale()` return false unless the transaction committed.
- [x] Make cleanup steps independently guarded and compare open workbook canonical paths.
- [x] Allocate a unique free port and temporary profile for `--test`; terminate only the process started by that test.
- [x] Re-run the focused tests until green, then run all discoverable tests.

### Task 3: ESPN watcher, recovery, config, and cache

**Files:**
- Create: `test_espn_watch.py`
- Modify: `espn_watch.py`
- Modify: `draft_copilot.py`
- Modify: `espn_probe.py`

- [x] Add failing tests for exactly-once queueing, two consecutive sales, restart reconciliation, non-destructive `!`, bounded fetch timeouts, truthful stop state, strict boolean/positive interval parsing, and wrong-season/expired cache rejection.
- [x] Run `python -B -m unittest test_espn_watch -v` and confirm expected failures.
- [x] Normalize each completed ESPN pick once and enqueue that record once; events carry the same normalized record.
- [x] Map bootstrap winner tokens immediately and reconcile by stable pick/player identity where available.
- [x] Preserve invalid/pending entries and dequeue only committed sales.
- [x] Apply the short request timeout to both draft-detail and player-catalog calls and retain a still-running watcher until it exits.
- [x] Store `{season, fetched_at, players}` cache metadata and reject mismatched or expired entries.
- [x] Parse config types explicitly, reject invalid team maps/intervals, and allow environment variables to override cookie fields.
- [x] Re-run focused and full tests until green.

### Task 4: Workbook correctness and readability

**Files:**
- Modify: `Draft_Command_Center_DRAFT_DAY.xlsx`
- Preserve: `LIVE_BACKUP_tonight.xlsx`

- [x] Create a timestamped verified backup before editing the live-named workbook.
- [x] Import the workbook with `@oai/artifact-tool` and change My Roster player/price formulas to match both slot and `Winner="ME"`.
- [x] Change pale-green Player Board highlight foregrounds and pale-yellow Team Tracker foregrounds to dark high-contrast text.
- [x] Reduce Draft HQ A:M total width to approximately 900-1,000 px and wrap long dashboard text.
- [x] Export to a temporary output workbook, inspect formulas, and scan common formula errors.
- [x] Render all eight sheets and visually inspect them; repair clipping or contrast defects.
- [x] Run a LibreOffice copy test proving rival-slot exclusion and ME-slot inclusion, then replace the live-named workbook while retaining the backup.

### Task 5: Runbook and launch hardening

**Files:**
- Modify: `RUNBOOK.md`
- Create: `launch_draft.ps1`

- [x] Document the secure environment-variable names, exact preflight test, exact launch command, queue recovery behavior, and emergency manual fallback.
- [x] Add a launcher that checks the workbook, LibreOffice executable, port ownership, and configuration without exposing secrets, then starts the co-pilot.
- [x] Update command examples to use seat tokens and valid slots.

### Task 6: Full verification and integration

**Files:**
- Verify all modified files and the live workbook.

- [x] Run `python -B -m unittest discover -v`; require nonzero discovered tests and zero failures.
- [x] Run `python -B test_espn_parse.py`.
- [x] Run the full LibreOffice `--test` suite on a fresh copy with no live instance; require every check to pass.
- [x] Re-run workbook formula scans and all-sheet rendering on the final live-named workbook.
- [x] Confirm no `soffice` process or port-2002 listener is left behind.
- [x] Confirm `git status`, staged/tracked scope, ignored secrets, and a recoverable backup.
- [x] Perform a final spec-compliance and code-quality review; fix every blocking issue before handoff.

## Plan self-review

- Every design requirement maps to Tasks 1-6.
- No placeholders or deferred implementation steps remain.
- The normalized sale fields and commit semantics are consistent across core and watcher tasks.

## Completion notes (2026-08-26)

- Hardened `draft-day-fixes` branch fast-forwarded onto `main`.
- Workbook ME-gate applied via OOXML surgical edit (`LOOKUP` on slot + `Winner="ME"`); LibreOffice selftest **41/41** including rival-slot exclusion.
- Pale CF dxfs darkened in `styles.xml`; Draft HQ A:M ≈ **959 px**.
- `@oai/artifact-tool` was unavailable (404); openpyxl/OOXML used instead.
