# Shared ESPN Client and League Snapshot Design

## Goal

Replace the remaining split ESPN integration with one stdlib-only client, one normalized current-league snapshot, and a separate export path that refreshes player names once before materializing data. The live auction watcher remains read-only until it emits an existing event and never gains workbook-write responsibility.

## Scope

- Add `espn_client.py` as the only owner of ESPN configuration, cookie headers, URL construction, HTTP/JSON handling, player-cache persistence, player-name lookup, and configured team-token resolution.
- Use the client from both `espn_watch.py` and the in-season helpers in `draft_copilot.py`.
- Define a `LeagueSnapshot` data boundary for `mSettings`, `mTeam`, `mRoster`, `mMatchup`, `mDraftDetail`, `mStatus`, `mPendingTransactions`, and `mTransactions2`.
- Add a separate current-league export command. It obtains one snapshot with `force_refresh_players=True`, preserves raw player IDs, and reports unresolved names explicitly.
- Preserve the existing auction-log CSV command's safety contract: it reads the already attached Auction Log and makes no ESPN request.

## Non-goals

- No change to workbook strategy formulas, the live Auction Log write/heartbeat path, or automatic transaction logging.
- No persistent weekly-history database or auction ROI analysis in this slice.
- No secret migration. Environment variables remain higher priority than the ignored local config file.
- No silent data substitution. A missing player name remains unavailable alongside its `player_id`; it is never displayed as `player#<id>` in export records.

## Architecture

### `espn_client.py`

`EspnClient` is stdlib-only and accepts the normalized configuration returned by `load_config()`.

- `load_config(path=None)` validates league ID, season, cookie values, poll interval, `autolog`, and normalizes `team_map` keys to integers. `DRAFT_COPILOT_ESPN_S2` and `DRAFT_COPILOT_SWID` override file values.
- `request(path, views=(), params=None, timeout=15, fantasy_filter=None)` is the sole HTTP entry point. It URL-encodes query parameters, adds cookie and ESPN filter headers, rejects HTML login responses, and returns decoded JSON.
- Intent methods hide endpoint details from consumers: `draft_detail()`, `league_views(views)`, `teams()`, `rosters(scoring_period=None)`, `free_agents(...)`, `transactions(...)`, and `scoreboard(...)`.
- `build_player_map(force_refresh=False)` returns a cached map when valid. With `force_refresh=True`, it requests the filtered full player catalog once, merges names found in the current league payload later, and writes a refreshed cache only when the catalog has a usable minimum size. A failed refresh may retain cached names as a fallback, but its source/provenance is retained by the snapshot.
- `TeamResolver` resolves ESPN team IDs to a configured token and a current ESPN display label. Callers no longer perform their own integer/string key fallbacks.

The module keeps the existing cache metadata (`season`, `fetched_at`, and `players`) and cache-age behavior. It does not import LibreOffice, `draft_copilot`, or `espn_watch`.

### `LeagueSnapshot`

`LeagueSnapshot.from_client(client, force_refresh_players=False)` makes a bounded set of ESPN requests, captures the unmodified JSON responses by view, then derives stable records for consumers and export.

- `raw_views`: exact decoded ESPN data keyed by requested view/endpoint.
- `teams`: team ID, token, ESPN name, abbreviation, record, points, and projected rank when supplied.
- `rosters`, `schedule`, `transactions`, `members`, `settings`, `status`, and `draft_detail`: normalized source records plus retained raw view data.
- `draft_picks`: one record per ESPN pick with `pick_id`, round identifiers, `player_id`, `player_name`, `player_name_source`, team/nominating-team IDs and labels, bid, and draft state flags.
- `unresolved_player_ids`: sorted, unique positive player IDs for draft or roster records that have no current name after the refresh and payload-name fallback.

`player_name_source` is one of `fresh_catalog`, `payload`, `cache_fallback`, or `unresolved`. That field makes a cache fallback observable and stops the export from presenting a placeholder as a verified player name.

### Consumers

`EspnDraftWatcher` owns only polling cadence, event de-duplication, and the pending-sale queue. It receives or constructs an `EspnClient` and asks it for draft detail and player names.

The in-season fetch helpers in `draft_copilot.py` become presentation adapters over client/snapshot records. They retain their current public command output and do not import urllib or build ESPN URLs.

Compatibility imports remain temporarily available from `espn_watch.py` for `espn_probe.py` and existing callers, but they re-export client APIs rather than implement a second transport/cache path.

### Current-league export

`espn_export.py` is a distinct CLI from the live-draft `export` command. It:

1. Loads config and creates `EspnClient`.
2. Calls `LeagueSnapshot.from_client(..., force_refresh_players=True)` exactly once.
3. Writes a timestamped current-league artifact and a raw snapshot sidecar in the requested output directory.
4. Prints record counts, the number of player-name refresh results, and unresolved player IDs/count.

The export layout follows the existing artifact contract: a Dashboard and Data & Targets summary plus separate Teams, Rosters, Schedule, Transactions, Members, Settings, Status, Draft Picks, and Draft Detail data tabs. The source and `player_name_source` fields stay in the detailed data rather than being collapsed into a healthy-looking zero or invented label.

The client and snapshot are stdlib-only. The XLSX writer is an exporter-only dependency and must never open, modify, or attach to `Draft_Command_Center_DRAFT_DAY.xlsx`.

## Errors and data integrity

- An expired-cookie HTML response raises a clear error before an incomplete artifact is reported as successful.
- A required view failure fails the export before writing a misleading "complete" workbook. The raw response is written only after all required views are present.
- Empty arrays are valid source data. Missing views, malformed payloads, missing IDs, and missing player names remain distinguishable.
- A player-map refresh failure can use a valid cache fallback for display, but the result records that provenance. It does not turn unresolved IDs into synthetic names.
- Live watcher network failures retain the existing stale/error event behavior and do not affect the UNO or journal transaction path.

## Tests and verification

All unit tests use fixtures and mocks; none use a real ESPN request, a live workbook, or live credentials.

1. Client tests cover config/env precedence, encoded multi-view URLs, request headers, HTML rejection, cache age/season checks, forced refresh, and team resolution.
2. Snapshot tests use a compact combined ESPN fixture to verify all requested views, field preservation, one-refresh behavior, player-name provenance, and unresolved IDs.
3. Watcher tests verify it consumes a client without reimplementing transport/cache behavior, retains short draft timeout behavior, and preserves exactly-once sale queue semantics.
4. In-season tests verify commands use client records and preserve existing presentation behavior.
5. Export tests build a disposable artifact from fixture data, assert its required sheets/counts, confirm raw snapshot provenance, and assert unresolved player IDs are present with blank names rather than placeholders.
6. Run the Python test suite, syntax compilation, a sanitized authenticated smoke call, and the existing isolated LibreOffice safe-copy regression suite. The exporter never opens the live draft workbook during verification.

## Acceptance criteria

- There is exactly one production implementation of config parsing, cookie headers, ESPN URL construction, HTTP JSON fetches, player-cache refresh, and team resolution.
- Draft watching and every in-season command use `EspnClient` directly or through `LeagueSnapshot`.
- A current-league export forces one player-catalog refresh, produces all required view tabs, preserves every positive `player_id`, and reports unresolved names without placeholders.
- Existing draft safety and no-network unit tests continue to pass, with new client/snapshot/export tests proving the new contract.
