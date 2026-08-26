# Draft-Day Emergency Remediation Design

## Goal

Make the auction co-pilot safe to run today by closing every confirmed review finding without redesigning the product or changing the workbook's strategy model.

## Chosen approach

Apply a narrow compatibility-preserving patch to the current Python/LibreOffice architecture. Keep the workbook as the source of strategy, but make Python mutations transactional and make ESPN sales flow through one normalized queue record. Patch the existing workbook in place only after a verified backup and copy-based test pass.

Alternatives rejected for today:

- Manual-only mode would be faster but would leave the requested fixes incomplete.
- A new event-store/service architecture would be cleaner long-term but is too risky for draft day.

## Python design

- Flatten one-cell UNO ranges before testing roster occupancy.
- Validate slots only for `ME`; rival slots are cleared. Make validation success return a consistent empty diagnostic.
- Snapshot Auction Log input cells before a sale write. If a write, recalc, or heartbeat check fails, restore the snapshot and report an uncommitted result.
- Journal, autosave, refresh, and dequeue only after a committed heartbeat.
- Normalize ESPN sales once with `pick_id`, `name`, `winner_token`, `winner_disp`, `price`, and `logged` fields. Both live polling and startup reconciliation use that record.
- Preserve unprocessable queue records rather than discarding them.
- Parse unique Team Tracker name fragments before resolving the player fragment.
- Give test runs a unique LibreOffice profile and free port, track process ownership, and compare open documents by canonical full path.
- Use bounded network timeouts, truthful watcher shutdown, season/timestamp cache metadata, and strict configuration parsing.
- Recover repeated stale locks using unique backup names and only treat the configured listener/profile as relevant.

## Workbook design

- My Roster player and price lookups require both matching slot and `Winner="ME"`.
- Preserve the existing visual language while changing pale-highlight text to a dark foreground.
- Reduce Draft HQ width to approximately 900-1,000 px at 100% zoom using narrower columns and wrapping, without deleting information.
- Render and inspect all eight sheets after export.

## Security and operations

- Add ignore rules before creating a local Git baseline so cookies, caches, journal, temp files, and live ESPN fixtures cannot be committed.
- Support cookie environment-variable overrides while retaining the ignored local JSON fallback for today's run.
- Document exact preflight, test, launch, recovery, and fallback commands.
- Do not print or commit credentials. Cookie rotation remains an operator action because ESPN must issue replacements.

## Acceptance gates

- Discoverable unit tests cover every confirmed failure and pass.
- A fresh isolated LibreOffice self-test passes every check.
- A rival sale with a roster slot cannot populate My Roster; a `ME` sale does.
- Two ESPN sales produce exactly two loggable queue records; restart reconciliation remains loggable.
- Heartbeat failure rolls back and remains pending.
- Test mode cannot reuse or terminate a live Draft Copilot instance.
- Workbook scan has no common formula errors and all eight rendered sheets are legible.
- Live configuration remains usable today and secrets are excluded from Git.

## Self-review

The scope is limited to confirmed review findings. It contains no placeholders, preserves the current command vocabulary and workbook strategy contract, and defines a measurable gate for every behavior being changed.
