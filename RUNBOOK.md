# DRAFT DAY RUNBOOK — Auction Co-Pilot

**One rule above all:** log *every* sale (every team, every player).
Your inflation factor, rival-threat engine and tier tracker starve on partial data.
At ~3 seconds per entry with the co-pilot, full-room logging is free. That's your edge.

---

## Launch (do this ~10 minutes before the draft)

1. Make sure **no LibreOffice window is open** (check system tray too).
2. Confirm this exact file sits next to `draft_copilot.py`:

   `Draft_Command_Center_DRAFT_DAY.xlsx`
3. **ESPN watch setup** (once per machine — see section below). Have `espn_config.json` ready.
4. Open a terminal in `C:\Users\Jared\Draft blaster` and run:

```
& "C:\Program Files\LibreOffice\program\python.exe" draft_copilot.py
```

You should see `workbook locked`, `schema OK`, `DRAFT CO-PILOT attached` + READY.
5. At the prompt type: `watch`
6. Window layout: **ESPN draft room center | Calc right | terminal bottom-left.**
7. Rename Team 2–14 now if you haven't (Team Tracker, blue column A). Winner codes
   `t2`–`t14` work by seat. **Also align `team_map` in espn_config.json to ESPN team IDs.**

## Automated dry-run (always on a COPY)

The co-pilot now runs each `--test` in an **isolated LibreOffice profile** with a
free TCP port. This means:

- Test runs **cannot** touch your live workbook.
- Test runs **cannot** terminate a running co-pilot session.
- If `soffice` crashed, the next `--test` automatically cleans up the orphan lock
  and starts fresh.

```
copy Draft_Command_Center_DRAFT_DAY.xlsx draft_test_copy.xlsx
& "C:\Program Files\LibreOffice\program\python.exe" draft_copilot.py --test draft_test_copy.xlsx
```

The test profile and port are created automatically — no manual cleanup needed.

## Commands

| Type this | What happens |
|---|---|
| `watch` | start ESPN on-block sync (loads HQ when nominee changes) |
| `watch off` | stop watcher |
| `block` | force-fetch current ESPN nominee → `who` briefing |
| `!` | log the last ESPN-announced sale into Auction Log |
| `last` | reprint last ESPN SOLD + suggested log line |
| `gibbs t7 34` | manual sale log |
| `who chase` | pre-bid briefing + Draft HQ panel |
| `nom` / `status` / `log` / `undo` | as before |

If watch fails or mismatches a name: type `who <name>` manually. Always works.

## ESPN live watch setup

1. Copy `espn_config.example.json` → `espn_config.json` (same folder).
2. Set `league_id` from your ESPN fantasy URL.
3. Grab cookies while logged into ESPN in Chrome (`espn_s2` + `SWID`).
4. Fill `team_map` (ESPN teamId → `ME`/`T2`…`T14`) to match Team Tracker seats.
5. Leave `autolog` **false** unless team_map is verified.
6. Optional cookie overrides (preferred if you don’t want secrets on disk):
   - `DRAFT_COPILOT_ESPN_S2`
   - `DRAFT_COPILOT_SWID`
7. Pre-check:

```
python espn_probe.py --refresh-players
```

   Expect `player map OK` (thousands of names). Then at draft lobby:

```
& "C:\Program Files\LibreOffice\program\python.exe" draft_copilot.py
```

   Type `watch`. When auction goes live, type **`block` once** to confirm HQ loads, then let watch drive nominees. Bid bumps = one line; `!` logs last sale; `last` reprints it. If polls die: `!! watch stale — use who`.

**Important:** Keep seat **A5 = `ME`** (workbook formulas key off that name for your budget). Rivals on A6–A18 can be ESPN abbrevs.

## Survival rules

- **Autosave:** every 10 sales + on quit. Still Ctrl+S if anything looks off.
- **Journal:** `journal.txt` logs SALE / UNDO / AUTOSAVE / session.
- Terminal died? Relaunch; Calc holds state; journal is the paper trail.
- Watch died / `watch stale`? Keep drafting with manual `who` + sale lines; `!` refuses already-logged players (use `undo` if you mis-logged).
- `!` only dequeues after a committed heartbeat — failed writes stay pending in the ESPN queue.
- Nuclear fallback: type sales into Auction Log B/D/E/F by hand.

## Preferred launch

```
.\launch_draft.ps1
```

Runs an isolated-copy selftest, then attaches to the live workbook. Use `-SkipTest` only if you already dry-ran.

## Pre-draft checklist

- [ ] Injury recheck vs espn.com/nfl/injuries
- [ ] Rivals renamed in Team Tracker
- [ ] `espn_config.json` filled; `python espn_probe.py` succeeds
- [ ] `team_map` verified against ESPN roster order
- [ ] Exact file: `Draft_Command_Center_DRAFT_DAY.xlsx`
- [ ] Automated dry-run on a **COPY** (uses isolated profile — see above)
- [ ] Live attach → `watch` → confirm "watch connected"
- [ ] Keep `LIVE_BACKUP_tonight.xlsx` as cold spare

---

*Selftest includes ESPN fixture parsers + workbook cascades. Re-run on a copy anytime with `--test`.*
