# Draft Export Command Implementation Plan

> **For agentic workers:** Execute task-by-task with TDD. Steps use checkbox syntax.

**Goal:** Add mid-draft-safe `export` command that dumps Auction Log to timestamped CSV and cross-checks current journal session.

**Architecture:** `Copilot.export()` + pure helpers; reuse live UNO `arr()` reads; harness tests without LibreOffice.

**Tech Stack:** Python 3 (LibreOffice bundled), unittest harnesses, csv module

---

### Task 1: Pure helpers + failing tests

- [ ] Add tests for `collect_auction_log_rows`, `winner_token_for`, `net_session_journal_sales`, `format_export_summary`, and `Copilot.export` harness
- [ ] Run tests — expect fail (symbols missing)
- [ ] Implement helpers + `Copilot.export` + command wiring
- [ ] Run tests — expect pass

### Task 2: Manual verification on workbook copy

- [ ] Copy workbook to safe test path
- [ ] Drive export via harness or small script using openpyxl/fixture grid OR LO --test if needed
- [ ] Show summary + CSV contents

Note: Live verification uses a copy only (`assert_safe_test_path` pattern). Prefer exercising `export` through the same harness path if LO attach is heavy; for end-to-end CSV proof, script that builds Copilot-like harness from openpyxl read of the copy is acceptable if full UNO attach is unavailable — primary path remains `cp.export()` on attached copy when LO works.
