# Draft Export Command Design

## Goal

Add a mid-draft-safe `export` command to the co-pilot that dumps the Auction Log to a timestamped CSV, cross-checks the current journal session, and prints a one-line integrity summary — without touching workbook state, budgets, or the journal.

## Decisions

- **Journal scope:** Current session only (entries after the latest `SESSION_START`), net of matching `UNDO` events.
- **Winner columns:** Both sheet display name and canonical token (`ME` / `T2`–`T14`).
- **Architecture:** `Copilot.export()` orchestrator + pure helpers; reuse the live UNO `self.arr(self.log, …)` connection (no second open / no openpyxl against the locked book).

## Architecture

- `Copilot.export(out_dir=SCRIPT_DIR, journal_path=JOURNAL_PATH)` — read-only orchestration.
- Pure helpers (unit-testable without LibreOffice):
  - `collect_auction_log_rows(grid)` — parse `A5:H…` style rows into dicts
  - `winner_token_for(disp, teams)` — reverse of `cp.teams`
  - `net_session_journal_sales(journal_text)` — SALE/UNDO net set after last `SESSION_START`
  - `write_export_csv(path, rows)` / `format_export_summary(rows, journal_only, spent)`
- Command loop: `elif low.startswith("export"): cp.export()` alongside `log` / `nom` / `status`.
- Update module docstring and `HELP`.

## Data flow

1. Read Auction Log via existing UNO: `self.arr(self.log, "A5:H%d" % LOG_LAST)`.
2. Keep rows where column B (player) is non-empty.
3. Per row fields: `pick`=A, `player`=B, `winner`=D, `price`=E, `slot`=F (may be empty), `winner_token` from reverse team map (empty string if unknown).
4. Read journal; from last `SESSION_START`, apply SALE (add player) / UNDO (remove player).
5. Write `draft_export_<YYYYMMDD_HHMMSS>.csv` under `out_dir` with header:
   `pick,player,winner,winner_token,price,slot`
6. Print one summary line via `say`:
   `export: N picks | spent ME=$x T2=$y … | journal-only: …`
   Spent keyed by `winner_token` when known, else display name. Missing journal players listed or `none`.

## Side effects / safety

- Read-only against the workbook (no writes, no recalc required for export).
- Does not append to `journal.txt`.
- Idempotent: each run creates a new timestamped file; prior exports untouched; log/budgets unchanged.

## Error handling

| Condition | Behavior |
|-----------|----------|
| Empty Auction Log | Header-only CSV; summary `0 picks` |
| Journal missing/unreadable | Warn; export CSV anyway; summary notes `(journal unavailable)` |
| CSV write failure | `!! export failed: …`; no success claim |
| UNO read failure | Propagates to `main()` `!! …` handler |

## Testing

Harness tests in `test_draft_copilot.py` (no LibreOffice):

1. Fake log grid → CSV contents, tokens, spent totals.
2. Temp journal with SESSION_START / SALE / UNDO → journal-only names.
3. Missing journal → export succeeds with unavailable note.
4. Two exports → two distinct files; harness cells unchanged.

Manual check: run `export` against a **copy** of the workbook (never the live book), show terminal summary and CSV sample.

## Out of scope

- Writing strategy or budget recomputation
- openpyxl dual-open of the live workbook
- Full-history journal replay across sessions
- Committing export CSVs to git
