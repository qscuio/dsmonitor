# TFTP Monitor App Design

Date: 2026-05-29

## Goal

Build a Windows desktop application that monitors `/tftpboot` on `10.55.2.114` over SSH/SFTP. When files are new or modified, the app downloads them to the local PC and then uploads them to `10.71.1.3:/home/tsl`. Source deletions are ignored.

## Confirmed Requirements

- Source host: `10.55.2.114`
- Source SSH user: `wei.li`
- Source path: `/tftpboot`
- Source auth: existing SSH key-based access is expected
- Destination host: `10.71.1.3`
- Destination SSH user: `tsl`
- Destination password: `tsl`
- Destination path: `/home/tsl`
- Transfer behavior: sync new and modified files only
- Delete behavior: do not delete local or destination files when source files are removed
- UX requirement: provide a GUI with status updates and transfer progress

## Recommended Approach

Use a Python desktop app with:

- `PySide6` for the GUI
- `asyncssh` or `paramiko` for SSH/SFTP operations
- a background sync worker separated from the GUI
- a local manifest file to track the last successful sync state

This approach keeps the app easy to run on Windows, avoids remote-side agents, and allows precise control over progress reporting, retries, and logging.

## Architecture

The app is split into four main parts:

1. GUI layer
   Presents status, logs, counters, controls, and settings.
2. Sync controller
   Starts and stops polling cycles, manages worker state, and coordinates retries.
3. Transfer engine
   Connects to source and destination hosts, scans files, downloads changes, uploads completed files, and emits progress events.
4. Persistence layer
   Stores settings, sync manifest, and recent logs on the local PC.

The GUI does not contain transfer logic. It only reacts to state changes emitted by the sync controller and transfer engine.

## User Interface

The main window should include:

- source connection status
- destination connection status
- current activity state: `idle`, `scanning`, `downloading`, `uploading`, `error`
- current file path
- progress bar for the active transfer
- counters for scanned files, changed files, successful syncs, failed syncs
- last successful scan time
- recent log/events panel
- `Start Monitoring` button
- `Stop Monitoring` button
- `Rescan Now` button
- `Open Local Folder` button
- settings controls for local cache path and poll interval

The UI should stay responsive while background work is running.

## Data Flow

Each sync cycle works as follows:

1. Connect to `wei.li@10.55.2.114` over SSH/SFTP.
2. Recursively scan `/tftpboot`.
3. Build a remote snapshot keyed by relative path with metadata such as size and modification time.
4. Compare the remote snapshot with the local manifest.
5. For each new or modified file:
   - download the file to the local cache
   - show transfer progress in the GUI
   - upload the local file to `tsl@10.71.1.3:/home/tsl/<relative-path>`
   - update the manifest only after both download and upload succeed
6. Emit summary status and wait until the next poll interval.

Path mapping preserves directory structure. For example:

- source: `/tftpboot/a/b.bin`
- local cache: `<cache>/a/b.bin`
- destination: `/home/tsl/a/b.bin`

## State And Persistence

The app stores local runtime state in an application data directory. At minimum:

- `settings.json` for configurable options
- `manifest.json` for last known successful file metadata
- `logs/` for operational logs

The manifest should track enough information to determine whether a file changed without retransferring every file on each cycle. A practical initial schema is:

- relative path
- source size
- source modification time
- last download timestamp
- last upload timestamp
- sync status

## Error Handling

- If the source scan fails, the app records the error, updates the GUI, and retries on the next cycle.
- If a file download fails, that file remains pending and is retried later.
- If a download succeeds but upload fails, the local cached file is kept and the file remains pending upload.
- If destination directories do not exist, the app creates them before upload.
- If authentication fails, the UI should identify which host failed and during which operation.
- A single file failure should not terminate the whole monitoring session.

## Retry Behavior

- Normal retries happen on the next poll cycle.
- Failed files remain eligible for retransmission until they succeed or the source file changes again.
- Manual `Rescan Now` triggers an immediate retry path without waiting for the next interval.

## Concurrency Model

Use a background worker thread or async task to keep all network activity off the UI thread. The UI receives structured events such as:

- connection opened
- scan started
- file changed
- download progress
- upload progress
- file synced
- file failed
- cycle completed

The initial implementation should process one file at a time for simpler progress reporting and lower risk. Parallel transfers can be added later if needed.

## Security And Credentials

- Source host should use the existing local SSH key configuration for `wei.li@10.55.2.114`.
- Destination host should initially support password authentication for `tsl@10.71.1.3`.
- Password handling should be scoped to the app settings and not hardcoded in transfer logic.
- The first version can persist settings locally; if needed later, password storage can move to the Windows credential store.

## Packaging And Runtime

The repo should support:

- a normal Python development workflow
- a single app entrypoint to launch the GUI
- later packaging as a Windows executable if desired

The initial deliverable is the runnable Python app in this repository, not an installer.

## Testing Strategy

Automated tests should cover:

- remote snapshot comparison logic
- manifest read/write behavior
- path mapping from `/tftpboot` to local cache and `/home/tsl`
- retry eligibility for failed transfers
- event/state transitions in the sync controller where practical

Manual verification should cover:

- source scan against `10.55.2.114`
- download to the local cache
- upload to `10.71.1.3:/home/tsl`
- GUI progress updates during active transfers
- recovery after transient SSH or SFTP failures

## Out Of Scope For First Version

- delete propagation
- bidirectional sync
- remote-side file system watchers
- service or tray-only background mode
- multi-file parallel upload/download scheduling
- advanced credential vault integration

## Implementation Notes

- Prefer polling over remote event hooks to avoid installing agents on `10.55.2.114`.
- Preserve relative paths exactly under both the local cache and destination root.
- Update the manifest only after the full two-step sync succeeds.
- Keep the transfer engine separate from GUI code so it can be unit tested cleanly.
