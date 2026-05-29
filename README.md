# TFTP Monitor

Desktop GUI app for monitoring multiple source directories on `wei.li@10.55.2.104`, caching changed files locally, and uploading them to `tsl@10.71.1.3:/home/tsl`.

## Defaults

- Source host: `10.55.2.104`
- Source user: `wei.li`
- Source directories:
  - `/tftpboot`
  - `/home/wei.li`
- Destination: `tsl@10.71.1.3:/home/tsl`
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

The packaged executable is created at `dist\TftpMonitor\TftpMonitor.exe`.

## Runtime Files

- Settings are stored in `%USERPROFILE%\AppData\Local\TftpMonitor\settings.json`
- Manifest state is stored in `%USERPROFILE%\AppData\Local\TftpMonitor\manifest.json`
- Downloaded files are cached under `%USERPROFILE%\AppData\Local\TftpMonitor\cache` unless you change the cache path in the GUI

## Behavior

- The GUI lets you edit source host, source user, source directories, destination host, destination user, destination password, destination root, poll interval, and local cache path
- New and modified files under `/tftpboot` and `/home/wei.li` are downloaded locally first, then uploaded to `/home/tsl/<relative-path>`
- Source root prefixes are not preserved on upload, so `/tftpboot/fw/a.bin` and `/home/wei.li/fw/a.bin` both map to `/home/tsl/fw/a.bin`
- If multiple source files map to the same destination path, the newest file wins for that sync cycle
- Files deleted from the source are not deleted locally or on the destination host
