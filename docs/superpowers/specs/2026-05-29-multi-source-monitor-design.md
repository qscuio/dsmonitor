# Multi-Source Monitor App Design

Date: 2026-05-29

## Goal

Extend the existing Windows desktop monitor app so it can watch multiple source directories on one source host, while still uploading all changed files to one destination root on one destination host.

## Confirmed Requirements

- Source host should be editable in the GUI
- Source user should be editable in the GUI
- Default source host: `10.55.2.104`
- Default monitored source directories:
  - `/tftpboot`
  - `/home/wei.li`
- Destination host should be editable in the GUI
- Destination user should be editable in the GUI
- Destination password should be editable in the GUI
- Destination root should be editable in the GUI
- Default destination host: `10.71.1.3`
- Default destination user: `tsl`
- Default destination password: `tsl`
- Default destination root: `/home/tsl`
- All changed files should upload into the single destination root
- Source root prefixes should not be preserved in destination paths
- If two source files map to the same destination path, the newest file wins
- Delete propagation remains disabled

## Recommended Approach

Keep the current polling-based desktop app and evolve the data model from a single source path to a list of monitored source directories. Continue using one destination root, but resolve per-cycle collisions by choosing the newest source file for each destination path before download and upload begin.

This keeps the feature aligned with the current architecture, avoids remote-side agents, and limits the change mostly to configuration, sync planning, manifest structure, and GUI editing.

## Architecture Changes

The app keeps the same top-level structure:

1. GUI layer
2. Sync controller
3. Transfer engine
4. Persistence layer

The main model change is:

- replace `source_path` with `source_directories: list[str]`

The sync engine should scan each configured source directory, produce one combined set of candidate files, resolve destination collisions, and then execute the selected download-upload operations.

## User Interface

The settings panel should now expose editable fields for both servers:

- source host
- source user
- monitored source directories
- destination host
- destination user
- destination password
- destination root
- poll interval
- local cache path

The monitored source directories should use a small editable list control with:

- `Add`
- `Edit`
- `Remove`

This is preferred over a delimiter-based text box because it is easier to validate and extend.

The status area should continue to show current activity, current file, transfer progress, summary counts, and recent events.

## Sync Behavior

Each sync cycle should do the following:

1. Connect to the source host.
2. Scan every configured source directory.
3. Build one combined set of changed-file candidates.
4. Map each source file to a destination path under the single destination root, without preserving the source root prefix.
5. If multiple candidates map to the same destination path, keep only the newest one and mark the others as skipped due to conflict.
6. Download each selected file into the local cache.
7. Upload each selected file to the destination host.
8. Update the manifest only after a download-upload pair succeeds.

Example mapping:

- source `/tftpboot/fw/a.bin` -> destination `/home/tsl/fw/a.bin`
- source `/home/wei.li/fw/a.bin` -> destination `/home/tsl/fw/a.bin`

If both files exist, the one with the newer modification time wins for that cycle.

Conflict resolution happens at the destination-path level, not just the filename level. This means `fw/a.bin` and `logs/a.bin` are distinct.

## Manifest And Settings Changes

The settings file should now store:

- source host
- source user
- source directories list
- destination host
- destination user
- destination password
- destination root
- poll interval
- local cache path

The manifest should now store enough information to explain and retry multi-source syncs:

- destination-relative path
- winning source directory
- source-relative path
- source size
- source modification time
- download status
- upload status

This allows the app to log which source root won a collision and preserve retry behavior after restart.

## Error Handling

- If one source directory scan fails, log that directory-specific failure and continue scanning the others when possible.
- If multiple files conflict on one destination path, log which source file won and which files were skipped.
- If download succeeds but upload fails, keep the local cache copy and mark it for retry.
- If destination authentication fails, show the destination host and operation in the GUI.
- A single file failure should not stop the whole monitoring session.

## Logging And UX Feedback

Recent events should include messages like:

- `Scanning /tftpboot`
- `Scanning /home/wei.li`
- `Conflict on fw/a.bin: chose /home/wei.li/fw/a.bin over /tftpboot/fw/a.bin`
- `Uploaded fw/a.bin to /home/tsl/fw/a.bin`

This is important because flattening multiple source roots into one destination root can otherwise look surprising to the user.

## Testing Strategy

Automated tests should add coverage for:

- settings persistence with multiple source directories
- destination-path conflict resolution
- newest-file-wins selection behavior
- manifest updates that include the winning source directory
- GUI settings handling for editable source and destination fields

Manual verification should cover:

- editing both hosts and paths in the GUI
- watching both default source directories on `10.55.2.104`
- conflict handling when both directories contain the same destination-relative path
- successful upload to the configured destination root
- desktop app and packaged `.exe` startup after the change

## Out Of Scope

- multiple destination servers
- preserving source root prefixes in destination paths
- delete propagation
- bidirectional sync
- remote-side file watchers

## Implementation Notes

- Keep the current single-destination model.
- Treat source directories as one logical source set for planning each cycle.
- Resolve conflicts before starting downloads so only winning files are transferred.
- Continue to use the local cache as the intermediate step before upload.
