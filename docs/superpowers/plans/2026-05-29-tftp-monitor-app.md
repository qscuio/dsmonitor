# TFTP Monitor App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows desktop GUI app that monitors `wei.li@10.55.2.114:/tftpboot`, downloads new or modified files to the local PC, and uploads them to `tsl@10.71.1.3:/home/tsl` with visible progress and retry-safe state.

**Architecture:** Use a Python package with a small domain core for settings, manifests, path mapping, and sync decisions, then layer a Qt GUI on top of a polling sync worker that emits structured progress events. Keep SSH/SFTP operations behind focused helper classes so the UI stays responsive and the core logic stays testable.

**Tech Stack:** Python 3.13, PySide6, Paramiko, pytest

---

## File Structure

- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\pyproject.toml`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\README.md`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\__init__.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\app.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\models.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\settings_store.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\manifest_store.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\pathing.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\sync_logic.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\ssh_sync.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\gui.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_package_smoke.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_pathing.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_manifest_store.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_sync_logic.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_gui.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_readme.py`

### Task 1: Bootstrap The Python Project

**Files:**
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\pyproject.toml`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\README.md`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\__init__.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_package_smoke.py`

- [ ] **Step 1: Write the failing packaging smoke test**

```python
from importlib.metadata import version


def test_package_metadata_is_exposed() -> None:
    assert version("tftp-monitor") == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_package_smoke.py -v`
Expected: FAIL because the package metadata does not exist yet.

- [ ] **Step 3: Write minimal project packaging**

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "tftp-monitor"
version = "0.1.0"
description = "Desktop monitor for TFTP source mirroring over SSH/SFTP."
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
  "PySide6>=6.9,<7",
  "paramiko>=3.5,<4",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3,<9",
]

[project.scripts]
tftp-monitor = "tftp_monitor.app:main"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.setuptools.package-dir]
"" = "src"

[tool.setuptools.packages.find]
where = ["src"]
```

```python
"""TFTP monitor application package."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pip install -e .[dev]`
Run: `python -m pytest tests/test_package_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.md src/tftp_monitor/__init__.py tests/test_package_smoke.py
git commit -m "feat: bootstrap tftp monitor project"
```

### Task 2: Implement Path Mapping Helpers

**Files:**
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\pathing.py`
- Test: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_pathing.py`

- [ ] **Step 1: Write the failing path mapping tests**

```python
from pathlib import Path

from tftp_monitor.pathing import build_destination_path, build_local_cache_path


def test_build_local_cache_path_preserves_relative_structure(tmp_path: Path) -> None:
    result = build_local_cache_path(tmp_path, "configs/a.bin")
    assert result == tmp_path / "configs" / "a.bin"


def test_build_destination_path_preserves_relative_structure() -> None:
    result = build_destination_path("/home/tsl", "images/fw.bin")
    assert result == "/home/tsl/images/fw.bin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pathing.py -v`
Expected: FAIL because `tftp_monitor.pathing` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from pathlib import Path, PurePosixPath


def build_local_cache_path(cache_root: Path, relative_path: str) -> Path:
    return cache_root.joinpath(*PurePosixPath(relative_path).parts)


def build_destination_path(destination_root: str, relative_path: str) -> str:
    return str(PurePosixPath(destination_root).joinpath(*PurePosixPath(relative_path).parts))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pathing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/pathing.py tests/test_pathing.py
git commit -m "feat: add sync path mapping helpers"
```

### Task 3: Implement Settings And Manifest Persistence

**Files:**
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\models.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\settings_store.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\manifest_store.py`
- Test: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_manifest_store.py`

- [ ] **Step 1: Write the failing persistence tests**

```python
from pathlib import Path

from tftp_monitor.manifest_store import ManifestStore
from tftp_monitor.models import FileRecord


def test_manifest_store_round_trips_records(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.json")
    record = FileRecord(
        relative_path="images/fw.bin",
        size=10,
        modified_time=20,
        download_status="pending",
        upload_status="pending",
    )

    store.save({"images/fw.bin": record})
    loaded = store.load()

    assert loaded["images/fw.bin"] == record
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manifest_store.py -v`
Expected: FAIL because the persistence modules and models do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import asdict, dataclass


@dataclass(slots=True)
class FileRecord:
    relative_path: str
    size: int
    modified_time: float
    download_status: str
    upload_status: str
```

```python
import json
from dataclasses import asdict
from pathlib import Path

from .models import FileRecord


class ManifestStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, FileRecord]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            key: FileRecord(**value)
            for key, value in raw.items()
        }

    def save(self, records: dict[str, FileRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: asdict(value) for key, value in records.items()}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_manifest_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/models.py src/tftp_monitor/settings_store.py src/tftp_monitor/manifest_store.py tests/test_manifest_store.py
git commit -m "feat: add local sync state persistence"
```

### Task 4: Implement Change Detection Logic

**Files:**
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\sync_logic.py`
- Test: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_sync_logic.py`

- [ ] **Step 1: Write the failing sync decision tests**

```python
from tftp_monitor.models import FileRecord, RemoteFile
from tftp_monitor.sync_logic import select_files_to_sync


def test_select_files_to_sync_returns_new_and_modified_files() -> None:
    remote_files = {
        "a.bin": RemoteFile(relative_path="a.bin", size=10, modified_time=20),
        "b.bin": RemoteFile(relative_path="b.bin", size=11, modified_time=22),
    }
    manifest = {
        "a.bin": FileRecord(
            relative_path="a.bin",
            size=9,
            modified_time=19,
            download_status="synced",
            upload_status="synced",
        )
    }

    result = select_files_to_sync(remote_files, manifest)

    assert [item.relative_path for item in result] == ["a.bin", "b.bin"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_logic.py -v`
Expected: FAIL because `RemoteFile` and `select_files_to_sync` do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass

from .models import FileRecord


@dataclass(slots=True)
class RemoteFile:
    relative_path: str
    size: int
    modified_time: float
```

```python
from .models import FileRecord, RemoteFile


def select_files_to_sync(
    remote_files: dict[str, RemoteFile],
    manifest: dict[str, FileRecord],
) -> list[RemoteFile]:
    changed: list[RemoteFile] = []
    for relative_path, remote in sorted(remote_files.items()):
        current = manifest.get(relative_path)
        if current is None:
            changed.append(remote)
            continue
        if current.size != remote.size or current.modified_time != remote.modified_time:
            changed.append(remote)
            continue
        if current.download_status != "synced" or current.upload_status != "synced":
            changed.append(remote)
    return changed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sync_logic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/models.py src/tftp_monitor/sync_logic.py tests/test_sync_logic.py
git commit -m "feat: add file change detection"
```

### Task 5: Implement SSH/SFTP Sync Engine

**Files:**
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\ssh_sync.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\models.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\manifest_store.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\sync_logic.py`

- [ ] **Step 1: Write the failing sync engine tests**

```python
from pathlib import Path

from tftp_monitor.models import AppSettings, FileRecord, RemoteFile
from tftp_monitor.ssh_sync import apply_successful_sync


def test_apply_successful_sync_marks_file_as_synced(tmp_path: Path) -> None:
    manifest = {}
    remote = RemoteFile(relative_path="configs/a.bin", size=15, modified_time=30)
    local_file = tmp_path / "configs" / "a.bin"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_bytes(b"payload")

    apply_successful_sync(manifest, remote)

    record = manifest["configs/a.bin"]
    assert record.size == 15
    assert record.download_status == "synced"
    assert record.upload_status == "synced"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_logic.py::test_apply_successful_sync_marks_file_as_synced -v`
Expected: FAIL because the helper function does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
from .models import FileRecord, RemoteFile


def apply_successful_sync(
    manifest: dict[str, FileRecord],
    remote_file: RemoteFile,
) -> None:
    manifest[remote_file.relative_path] = FileRecord(
        relative_path=remote_file.relative_path,
        size=remote_file.size,
        modified_time=remote_file.modified_time,
        download_status="synced",
        upload_status="synced",
    )
```

Then expand `ssh_sync.py` to provide:

```python
class SshSyncService:
    def scan_source(self) -> dict[str, RemoteFile]:
        """Return the current recursive snapshot of /tftpboot."""

    def download_file(self, remote_file: RemoteFile, local_path: Path, progress: ProgressCallback) -> None:
        """Copy one source file to the local cache and emit byte progress."""

    def upload_file(self, local_path: Path, remote_file: RemoteFile, progress: ProgressCallback) -> None:
        """Copy one local file to /home/tsl/<relative-path> and emit byte progress."""

    def sync_once(self) -> SyncCycleResult:
        """Run one scan-download-upload cycle and return counters plus errors."""
```

- [ ] **Step 4: Run targeted and full tests**

Run: `python -m pytest tests/test_sync_logic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/models.py src/tftp_monitor/sync_logic.py src/tftp_monitor/ssh_sync.py tests/test_sync_logic.py
git commit -m "feat: add ssh sync engine"
```

### Task 6: Implement The GUI And App Entrypoint

**Files:**
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\gui.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\app.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\models.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\settings_store.py`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_gui.py`

- [ ] **Step 1: Write the failing GUI state test**

```python
from tftp_monitor.gui import format_status_summary
from tftp_monitor.models import SyncCycleResult


def test_format_status_summary_includes_core_counts() -> None:
    result = SyncCycleResult(scanned_files=5, changed_files=2, synced_files=2, failed_files=1)
    assert format_status_summary(result) == "Scanned 5 | Changed 2 | Synced 2 | Failed 1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gui.py -v`
Expected: FAIL because the GUI helper and result model do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def format_status_summary(result: SyncCycleResult) -> str:
    return (
        f"Scanned {result.scanned_files} | "
        f"Changed {result.changed_files} | "
        f"Synced {result.synced_files} | "
        f"Failed {result.failed_files}"
    )
```

Then build the main window with:

```python
class MainWindow(QMainWindow):
    def __init__(self, settings_store: SettingsStore, manifest_store: ManifestStore) -> None:
        """Create the desktop window, wire signals, and own the worker lifecycle."""
```

Required widgets:
- source status label
- destination status label
- activity label
- current file label
- progress bar
- summary label
- recent events list
- poll interval spin box
- local cache path field
- start button
- stop button
- rescan button
- open folder button

- [ ] **Step 4: Run targeted and full tests**

Run: `python -m pytest tests/test_gui.py -v`
Run: `python -m pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/app.py src/tftp_monitor/gui.py src/tftp_monitor/models.py src/tftp_monitor/settings_store.py tests/test_gui.py
git commit -m "feat: add desktop monitor gui"
```

### Task 7: Document Usage And Verify The App

**Files:**
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\README.md`
- Create: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_readme.py`

- [ ] **Step 1: Write the failing documentation expectation test**

```python
from pathlib import Path


def test_readme_mentions_start_command() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "python -m tftp_monitor.app" in readme
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_readme.py -v`
Expected: FAIL because the README does not contain the launch instructions yet.

- [ ] **Step 3: Write minimal documentation**

Include:

```md
## Run

python -m pip install -e .[dev]
python -m tftp_monitor.app
```

Also document:
- default source and destination hosts
- how the local cache works
- where settings and manifest files are stored
- current limitation that delete propagation is disabled

- [ ] **Step 4: Run final verification**

Run: `python -m pytest -v`
Run: `python -m tftp_monitor.app`
Expected: tests PASS and the desktop window opens without traceback.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_readme.py
git commit -m "docs: add tftp monitor usage guide"
```

## Self-Review

- Spec coverage checked: the plan includes GUI, polling sync, local caching, progress reporting, manifest persistence, retry-safe sync decisions, and `/home/tsl` destination mapping.
- Placeholder scan checked: vague ellipsis stubs were replaced with explicit responsibilities and missing test files were added to the file list.
- Type consistency checked: the plan uses the same `FileRecord`, `RemoteFile`, `SshSyncService`, and `SyncCycleResult` names across tasks.
