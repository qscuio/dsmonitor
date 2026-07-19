# dsmonitor

Desktop GUI app for monitoring files or whole directories between `wei.li@10.55.2.104` and `tsl@10.71.1.3`. The normal Monitor tab syncs `10.55.2.104` to `10.71.1.3`; the Reverse Monitor tab syncs `10.71.1.3` back to `10.55.2.104`.

## Defaults

- Source host: `10.55.2.104`
- Source user: `wei.li`
- Monitor path: choose one or more files, wildcard patterns, or directories to watch, such as `/tftpboot/*.x, /tftpboot/V8500*`, `/tftpboot/V8888_dev.x`, or `/tftpboot`
- Destination: `tsl@10.71.1.3:/home/tsl`
- Reverse monitor source: `tsl@10.71.1.3:/home/tsl`
- Reverse destination: `wei.li@10.55.2.104:/tftpboot`
- Poll interval: `5` seconds

## Run

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
.\.venv\Scripts\python -m tftp_monitor.app
```

## Build EXE

```powershell
.\scripts\build_exe.ps1
```

The packaged executable is created as a single file at `dist\dsmonitor.exe`.

## Runtime Files

- Settings are stored in `%USERPROFILE%\Documents\dsmonitor\settings.json`
- Forward manifest state is stored in `%USERPROFILE%\Documents\dsmonitor\manifest.json`
- Reverse manifest state is stored in `%USERPROFILE%\Documents\dsmonitor\reverse-manifest.json`
- Downloaded files are cached directly under `%USERPROFILE%\Documents\dsmonitor` unless you change the local path in Settings

## Behavior

- The Monitor tab watches `10.55.2.104` and sends changed files to `10.71.1.3`
- The Reverse Monitor tab watches `10.71.1.3` and sends changed files to `10.55.2.104`
- Use the Monitor Path input to choose remote files, wildcard patterns, or directories; separate multiple paths with commas, semicolons, or new lines
- Wildcard monitor paths like `/tftpboot/*.x` or `/tftpboot/V8500*` scan the parent directory and sync only matching files
- `Use Folder` appends the currently listed directory to the Monitor Path input
- When a directory is monitored, files under nested subdirectories are scanned recursively
- The status panel shows source, local cache, and `10.71.1.3` target path, modified time, and file size
- Settings contain source host/user/password/key, destination host/user/password/key, destination root, poll interval, and local cache path
- The Transfer tab supports drag-and-drop manual transfers:
  - It lists files from `10.55.2.104:/tftpboot` and `10.71.1.3:/home/tsl`
  - Drag local files from Windows Explorer onto the `10.71.1.3 Target` list to upload automatically
  - Drag a `10.55.2.104` file onto `Local` to download it
  - Drag a `10.71.1.3` file onto `Local` to download it
  - Drag a `10.55.2.104` file onto the `10.71.1.3 Target` list to copy it across servers
- Transfer operations run in the background so the GUI remains responsive while files move
- Monitor and Transfer progress bars show transfer speed
- Minimizing or closing the window keeps dsmonitor running in the Windows tray; use the tray menu to show or exit the app
- When dsmonitor is hidden in the tray, a file update shows a Windows tray notification
- Configured monitor paths are checked directly; unrelated files outside those files or directories are not uploaded
- The first successful scan creates a baseline and uploads nothing
- Only files created or modified after that baseline are downloaded locally first, then uploaded to `/home/tsl/<relative-path>`
- Source root prefixes are not preserved on upload, so monitoring `/tftpboot` maps `/tftpboot/fw/a.bin` to `/home/tsl/fw/a.bin`
- If multiple source files map to the same destination path, the newest file wins for that sync cycle
- Files deleted from the source are not deleted locally or on the destination host
