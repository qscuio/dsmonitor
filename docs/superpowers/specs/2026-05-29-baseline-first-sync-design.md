# Baseline-First Sync Design

Date: 2026-05-29

## Goal

Change the monitor app so that the first successful scan creates a baseline only. Existing files present at initial connection should not be uploaded. Only files created or modified after that baseline should be synced.

## Confirmed Requirements

- The first successful scan should upload nothing
- The first successful scan should still record the current source state
- Later scans should sync only new or modified files
- `Rescan Now` should not backfill existing files from before the baseline
- No separate manual full-backfill feature is needed
- Existing multi-source behavior remains in place
- Existing newest-file-wins conflict behavior remains in place

## Recommended Approach

Keep the current manifest-driven design and add one special case:

- when the manifest is empty, treat the first successful scan as a baseline creation pass instead of a transfer pass

This is more reliable than timestamp cutoffs because it uses the exact scanned file metadata rather than assuming source mtimes reflect the moment monitoring began.

## Sync Behavior

### First Successful Scan

1. Scan all configured source directories
2. Build the combined multi-source candidate set
3. Resolve destination conflicts with newest-file-wins
4. Write the winning files into the manifest as already synced
5. Upload nothing
6. Emit a completion event indicating that a baseline was created

Example GUI/log message:

- `Baseline created: 284 files indexed, 0 uploaded`

### Later Scans

After the baseline exists:

- new files should sync
- modified files should sync
- unchanged files already in the manifest should not sync
- `Rescan Now` should follow the same rule set

## Manifest Behavior

The manifest remains the source of truth for known files.

On baseline creation:

- each winning file should be stored in the manifest
- each entry should be marked as already synced
- no local download cache is required for baseline-only entries

This means the second and later scans can compare current source metadata against the stored baseline and transfer only changed files.

## Error Handling

- If the first scan fails completely, no baseline should be committed.
- If some source directories fail during the first scan, only commit a baseline if the app considers the scan successful enough to trust the indexed result.
- If conflict resolution happens during baseline creation, log the winning file exactly as in normal sync mode.
- If the baseline is created successfully, the GUI should show that uploads were intentionally skipped rather than silently doing nothing.

## Testing Strategy

Automated tests should add coverage for:

- first sync with empty manifest creates baseline and performs no upload
- second sync after baseline uploads only changed files
- `Rescan Now` after baseline does not backfill unchanged files
- baseline creation still respects newest-file-wins conflict resolution

Manual verification should cover:

- launching the app with an empty manifest
- confirming no existing files upload on first connection
- creating a new top-level file afterward and confirming that it does upload

## Out Of Scope

- manual full backfill
- timestamp-based filtering
- resetting baseline from the GUI

## Implementation Notes

- The baseline rule should live in the sync engine, not the GUI.
- The GUI should only display the baseline-created result and log messages.
- Keep the behavior deterministic: empty manifest means baseline mode, non-empty manifest means normal incremental mode.
