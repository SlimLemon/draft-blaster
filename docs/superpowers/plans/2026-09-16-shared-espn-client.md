# Shared ESPN Client Implementation Plan

**Spec:** ../specs/2026-09-16-shared-espn-client-design.md
**Execution:** Inline in the current checkout; approved by the user's execute/continue instructions.

## Contracts and verification

- [ ] Add tests at the HTTP boundary using real clients and temporary caches: environment override before validation, encoded repeated views, HTML rejection, cache season/age, forced refresh once, per-player provenance, failed refresh does not renew cache.
- [ ] Implement stdlib espn_client.py with EspnClient, TeamResolver, load_config, and compatibility functions. Configured season owns the cache; transport errors contain no credentials.
- [ ] Add snapshot tests before implementing league_snapshot.py. Preserve exact combined league response once with requested-view metadata; do not pretend overlapping views were separate responses. Missing collections remain distinguishable from empty collections. Include negative D/ST IDs, payload identity, unknown positive IDs, bid values, and all source fields.
- [ ] Migrate watcher/probe and copilot to client intent methods. Keep existing watcher events, queues, timeouts and Auction Log CSV safety behavior. Test consumers with a real client and fake network.
- [ ] Add a snapshot export entry point which forces one catalog refresh, writes private raw and normalized JSON, and feeds an isolated workbook renderer. Fail before publishing on required-source errors. Never change the live draft workbook.
- [ ] Run offline tests, syntax checks, sanitized authenticated read/export, workbook inspection and safe-copy LibreOffice regression. Review and commit the intended scope only.

## File ownership

espn_client.py owns auth/config/transport/URLs/player cache/team identity.
league_snapshot.py owns normalized records and coverage.
espn_watch.py owns draft events.
draft_copilot.py owns terminal presentation and existing UNO behavior.
espn_export.py owns export orchestration; an isolated JS renderer owns XLSX formatting.
Tests mock HTTP only where practical and block accidental real network access.

## Implementation details

Use one filtered catalog request per forced refresh. Cache stores records plus per-record fetch timestamps so partial refresh never relabels old names fresh. Return the legacy name-map tuple for compatibility; expose record provenance on the client for snapshot enrichment. Resolve fresh catalog, then payload, then valid cache fallback; never synthesize an export name.

Use a combined league request with all eight approved views. Retain the exact combined response in raw_snapshot and list requested views separately. Require settings/status/teams/draftDetail/schedule; transaction absence must appear as unavailable rather than an empty success. Use explicit collection status metadata when ESPN omits optional transaction fields.

Run python -m pytest -q, python -m py_compile on changed modules, and the LibreOffice bundled Python safe-copy test. Export to a unique ignored output directory. Inspect staged paths and diff before commit.

