# Multi-Source Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the desktop app to monitor multiple configurable source directories on one source server, expose editable source and destination connection settings in the GUI, and upload all winning files into one destination root using newest-file-wins conflict resolution.

**Architecture:** Evolve the app settings model from one source path to a list of source directories, add a planning step in the sync engine that flattens files from all roots into one destination namespace, and update the PySide6 GUI to edit both server endpoints plus the source directory list. Keep the current polling worker, manifest persistence, and executable packaging flow.

**Tech Stack:** Python 3.13, PySide6, Paramiko, pytest, PyInstaller

---

## File Structure

- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\models.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\settings_store.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\manifest_store.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\pathing.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\sync_logic.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\ssh_sync.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\gui.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\README.md`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_manifest_store.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_sync_logic.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_ssh_sync.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_gui.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_readme.py`

### Task 1: Convert Settings To Multi-Source Configuration

**Files:**
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\models.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\settings_store.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_manifest_store.py`

- [ ] **Step 1: Write the failing settings tests**

```python
from pathlib import Path

from tftp_monitor.models import AppSettings
from tftp_monitor.settings_store import SettingsStore


def test_default_settings_include_both_source_directories() -> None:
    settings = AppSettings.default(Path("C:/tmp/tftp-monitor"))

    assert settings.source_directories == ["/tftpboot", "/home/wei.li"]


def test_settings_store_round_trips_multiple_source_directories(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    settings = AppSettings.default(tmp_path)
    settings.source_directories = ["/tftpboot", "/home/wei.li", "/opt/images"]
    settings.source_host = "10.55.2.200"
    settings.destination_host = "10.71.1.9"

    store.save(settings)
    loaded = store.load()

    assert loaded.source_directories == ["/tftpboot", "/home/wei.li", "/opt/images"]
    assert loaded.source_host == "10.55.2.200"
    assert loaded.destination_host == "10.71.1.9"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python -m pytest tests/test_manifest_store.py -v`
Expected: FAIL because `AppSettings` still exposes only `source_path`.

- [ ] **Step 3: Write minimal implementation**

Update `AppSettings` so it carries:

```python
source_host: str
source_user: str
source_directories: list[str]
destination_host: str
destination_user: str
destination_password: str
destination_path: str
```

Use these defaults:

```python
source_host="10.55.2.104"
source_user="wei.li"
source_directories=["/tftpboot", "/home/wei.li"]
destination_host="10.71.1.3"
destination_user="tsl"
destination_password="tsl"
destination_path="/home/tsl"
```

Update `SettingsStore.load()` and `SettingsStore.save()` so `source_directories` round-trips as a JSON list and old missing-field loads still fall back to `AppSettings.default(...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python -m pytest tests/test_manifest_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/models.py src/tftp_monitor/settings_store.py tests/test_manifest_store.py
git commit -m "feat: support multiple source directories in settings"
```

### Task 2: Add Multi-Source Conflict Resolution Logic

**Files:**
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\models.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\pathing.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\sync_logic.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_sync_logic.py`

- [ ] **Step 1: Write the failing multi-source planning tests**

```python
from tftp_monitor.models import FileRecord, RemoteFile
from tftp_monitor.sync_logic import build_sync_plan


def test_build_sync_plan_keeps_newest_conflicting_file() -> None:
    remote_files = [
        RemoteFile(source_directory="/tftpboot", relative_path="fw/a.bin", destination_relative_path="fw/a.bin", size=10, modified_time=20),
        RemoteFile(source_directory="/home/wei.li", relative_path="fw/a.bin", destination_relative_path="fw/a.bin", size=11, modified_time=30),
    ]

    plan = build_sync_plan(remote_files, {})

    assert len(plan.selected) == 1
    assert plan.selected[0].source_directory == "/home/wei.li"
    assert plan.skipped_conflicts[0].source_directory == "/tftpboot"


def test_build_sync_plan_respects_manifest_for_unchanged_winner() -> None:
    remote_files = [
        RemoteFile(source_directory="/home/wei.li", relative_path="fw/a.bin", destination_relative_path="fw/a.bin", size=11, modified_time=30),
    ]
    manifest = {
        "fw/a.bin": FileRecord(
            destination_relative_path="fw/a.bin",
            source_directory="/home/wei.li",
            source_relative_path="fw/a.bin",
            size=11,
            modified_time=30,
            download_status="synced",
            upload_status="synced",
        )
    }

    plan = build_sync_plan(remote_files, manifest)

    assert plan.selected == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python -m pytest tests/test_sync_logic.py -v`
Expected: FAIL because `RemoteFile`, `FileRecord`, and `build_sync_plan` do not support multi-source metadata yet.

- [ ] **Step 3: Write minimal implementation**

Change the core models to:

```python
@dataclass(slots=True)
class RemoteFile:
    source_directory: str
    relative_path: str
    destination_relative_path: str
    size: int
    modified_time: float
```

```python
@dataclass(slots=True)
class FileRecord:
    destination_relative_path: str
    source_directory: str
    source_relative_path: str
    size: int
    modified_time: float
    download_status: TransferStatus
    upload_status: TransferStatus
```

Add a small planning result:

```python
@dataclass(slots=True)
class SyncPlan:
    selected: list[RemoteFile]
    skipped_conflicts: list[RemoteFile]
```

Then implement `build_sync_plan(...)` to:
- group candidates by `destination_relative_path`
- keep the newest `modified_time` winner
- place losers into `skipped_conflicts`
- skip the winner if the manifest already records that same file as fully synced and unchanged

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python -m pytest tests/test_sync_logic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/models.py src/tftp_monitor/pathing.py src/tftp_monitor/sync_logic.py tests/test_sync_logic.py
git commit -m "feat: add multi-source sync planning"
```

### Task 3: Update The SSH Sync Engine For Multiple Source Roots

**Files:**
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\manifest_store.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\ssh_sync.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_ssh_sync.py`

- [ ] **Step 1: Write the failing sync-engine test**

```python
from pathlib import Path

from tftp_monitor.manifest_store import ManifestStore
from tftp_monitor.models import AppSettings, RemoteFile
from tftp_monitor.ssh_sync import SshSyncService


def test_sync_once_logs_conflicts_and_uploads_only_newest_file(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    events = []
    source = FakeMultiSourceGateway(
        {
            "/tftpboot": {"fw/a.bin": RemoteFile(source_directory="/tftpboot", relative_path="fw/a.bin", destination_relative_path="fw/a.bin", size=10, modified_time=20)},
            "/home/wei.li": {"fw/a.bin": RemoteFile(source_directory="/home/wei.li", relative_path="fw/a.bin", destination_relative_path="fw/a.bin", size=11, modified_time=30)},
        }
    )
    destination = FakeDestinationGateway()
    service = SshSyncService(settings, manifest_store, source, destination, lambda event: events.append(event.message))

    result = service.sync_once()

    assert result.synced_files == 1
    assert result.failed_files == 0
    assert any("Conflict on fw/a.bin" in message for message in events)
    assert destination.uploads == [(settings.local_cache_dir / "fw" / "a.bin", "/home/tsl")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python -m pytest tests/test_ssh_sync.py -v`
Expected: FAIL because the service still scans only one root and does not emit conflict messages.

- [ ] **Step 3: Write minimal implementation**

Update `SshSyncService` to:
- iterate over `settings.source_directories`
- merge all scanned files into one candidate list
- call `build_sync_plan(...)`
- emit conflict log messages like `Conflict on fw/a.bin: chose /home/wei.li/fw/a.bin over /tftpboot/fw/a.bin`
- download winners from their original source directory
- upload winners to `destination_path / destination_relative_path`
- persist manifest records keyed by `destination_relative_path`

Adjust the source gateway interface so `scan()` and `download()` accept a source directory argument:

```python
def scan(self, source_directory: str) -> dict[str, RemoteFile]:
    ...

def download(self, source_directory: str, remote_file: RemoteFile, local_path: Path, progress: ProgressCallback) -> None:
    ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python -m pytest tests/test_ssh_sync.py -v`
Run: `.\.venv\Scripts\python -m pytest tests/test_sync_logic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/manifest_store.py src/tftp_monitor/ssh_sync.py tests/test_ssh_sync.py tests/test_sync_logic.py
git commit -m "feat: sync multiple source roots into one destination"
```

### Task 4: Make Source And Destination Endpoints Editable In The GUI

**Files:**
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\gui.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_gui.py`

- [ ] **Step 1: Write the failing GUI helper test**

```python
from pathlib import Path

from tftp_monitor.models import AppSettings
from tftp_monitor.gui import format_source_directory_summary


def test_format_source_directory_summary_joins_multiple_roots() -> None:
    settings = AppSettings.default(Path("C:/tmp/tftp-monitor"))

    assert format_source_directory_summary(settings.source_directories) == "/tftpboot; /home/wei.li"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python -m pytest tests/test_gui.py -v`
Expected: FAIL because the GUI still hardcodes one source root and read-only endpoint labels.

- [ ] **Step 3: Write minimal implementation**

Add editable widgets for:

```python
self.source_host_edit = QLineEdit()
self.source_user_edit = QLineEdit()
self.source_directories_list = QListWidget()
self.destination_host_edit = QLineEdit()
self.destination_user_edit = QLineEdit()
self.destination_password_edit = QLineEdit()
self.destination_path_edit = QLineEdit()
```

Add buttons:

```python
self.add_source_dir_button = QPushButton("Add")
self.edit_source_dir_button = QPushButton("Edit")
self.remove_source_dir_button = QPushButton("Remove")
```

Update form persistence so `_current_settings_from_form()` returns:

```python
source_host=self.source_host_edit.text().strip()
source_user=self.source_user_edit.text().strip()
source_directories=[self.source_directories_list.item(i).text() for i in range(self.source_directories_list.count())]
destination_host=self.destination_host_edit.text().strip()
destination_user=self.destination_user_edit.text().strip()
destination_password=self.destination_password_edit.text()
destination_path=self.destination_path_edit.text().strip()
```

Require at least one source directory and non-empty host/path fields before start.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python -m pytest tests/test_gui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/gui.py tests/test_gui.py
git commit -m "feat: make monitor endpoints editable in gui"
```

### Task 5: Update Documentation And Rebuild The Executable

**Files:**
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\README.md`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_readme.py`

- [ ] **Step 1: Write the failing README test**

```python
from pathlib import Path


def test_readme_mentions_both_default_source_directories() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "/tftpboot" in readme
    assert "/home/wei.li" in readme
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python -m pytest tests/test_readme.py -v`
Expected: FAIL because the README still documents one source root.

- [ ] **Step 3: Write minimal documentation**

Document:
- both default monitored source directories
- editable source and destination connection settings
- newest-file-wins conflict behavior
- flat upload target under `/home/tsl`
- the existing executable build command:

```powershell
.\scripts\build_exe.ps1
```

- [ ] **Step 4: Run final verification**

Run: `.\.venv\Scripts\python -m pytest -v`
Run: `.\scripts\build_exe.ps1`
Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$exe = Resolve-Path '.\dist\TftpMonitor\TftpMonitor.exe'
$p = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3
if ($p.HasExited) { throw \"Executable exited with $($p.ExitCode)\" }
Stop-Process -Id $p.Id -Force
```

Expected: tests PASS, build succeeds, executable stays up during smoke launch.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_readme.py
git commit -m "docs: describe multi-source monitor behavior"
```

## Self-Review

- Spec coverage checked: settings, GUI, conflict resolution, manifest changes, multi-root scanning, and executable verification are all covered by explicit tasks.
- Placeholder scan checked: all tasks include concrete file paths, test names, commands, and implementation targets.
- Type consistency checked: `source_directories`, `destination_relative_path`, `source_directory`, `SyncPlan`, and the updated gateway signatures are used consistently across later tasks.
