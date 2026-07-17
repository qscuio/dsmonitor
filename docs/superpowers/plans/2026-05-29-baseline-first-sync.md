# Baseline-First Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the monitor app so the first successful scan creates a baseline only, uploading nothing, while later scans sync only new or modified files.

**Architecture:** Keep the current multi-source manifest-driven sync design, but add a baseline branch in the sync engine that records the first successful scan as already-known state. Preserve the top-level-only source scan behavior so the baseline completes quickly on large home directories.

**Tech Stack:** Python 3.13, PySide6, Paramiko, pytest, PyInstaller

---

## File Structure

- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\ssh_sync.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_ssh_sync.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\README.md`

### Task 1: Add Baseline-Only First Sync Behavior

**Files:**
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\tests\test_ssh_sync.py`
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\src\tftp_monitor\ssh_sync.py`

- [ ] **Step 1: Write the failing baseline tests**

```python
def test_first_sync_creates_baseline_without_upload(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    events: list[str] = []
    source = FakeMultiSourceGateway(
        {
            "/tftpboot": {
                "existing.bin": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="existing.bin",
                    destination_relative_path="existing.bin",
                    size=10,
                    modified_time=20,
                )
            },
            "/home/wei.li": {},
        }
    )
    destination = FakeDestinationGateway()
    service = SshSyncService(
        settings=settings,
        manifest_store=manifest_store,
        source_gateway=source,
        destination_gateway=destination,
        event_callback=lambda event: events.append(event.message),
    )

    result = service.sync_once()

    assert result.scanned_files == 1
    assert result.changed_files == 0
    assert result.synced_files == 0
    assert destination.uploads == []
    assert manifest_store.load()["existing.bin"].upload_status == "synced"
    assert any("Baseline created" in message for message in events)


def test_second_sync_after_baseline_uploads_only_new_file(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    source = FakeMultiSourceGateway(
        {
            "/tftpboot": {
                "existing.bin": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="existing.bin",
                    destination_relative_path="existing.bin",
                    size=10,
                    modified_time=20,
                )
            },
            "/home/wei.li": {},
        }
    )
    destination = FakeDestinationGateway()
    service = SshSyncService(settings, manifest_store, source, destination)
    baseline = service.sync_once()
    assert baseline.synced_files == 0

    source.snapshots["/home/wei.li"]["2"] = RemoteFile(
        source_directory="/home/wei.li",
        relative_path="2",
        destination_relative_path="2",
        size=4,
        modified_time=30,
    )

    result = service.sync_once()

    assert result.changed_files == 1
    assert result.synced_files == 1
    assert destination.uploads[-1] == (settings.local_cache_dir / "2", "/home/tsl")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python -m pytest tests/test_ssh_sync.py -v`
Expected: FAIL because the current first sync uploads existing files instead of creating a baseline.

- [ ] **Step 3: Write minimal implementation**

Add a baseline branch near the top of `sync_once()`:

```python
manifest = self.manifest_store.load()
is_first_sync = not manifest
```

After `plan = build_sync_plan(...)`, if `is_first_sync` is true:

```python
for remote_file in plan.selected:
    apply_successful_sync(manifest, remote_file)
self.manifest_store.save(manifest)
result.changed_files = 0
result.synced_files = 0
result.activity = "idle"
self._emit(
    "baseline_created",
    f"Baseline created: {len(plan.selected)} files indexed, 0 uploaded",
    activity="idle",
)
self._emit(
    "cycle_completed",
    "Sync cycle completed",
    activity="idle",
)
return result
```

Keep conflict logging in baseline mode so the winning file is still visible in the event log. Preserve the current top-level-only `_scan_remote_tree(...)` implementation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python -m pytest tests/test_ssh_sync.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tftp_monitor/ssh_sync.py tests/test_ssh_sync.py
git commit -m "feat: baseline first sync without backfill"
```

### Task 2: Update Documentation And Verify Build

**Files:**
- Modify: `C:\Users\LiWei(WeiLi)\Documents\zebos_porting\README.md`

- [ ] **Step 1: Write the failing README expectation test**

```python
def test_readme_mentions_first_scan_baseline_behavior() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "first successful scan creates a baseline" in readme
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python -m pytest tests/test_readme.py -v`
Expected: FAIL because the README does not describe baseline-first sync yet.

- [ ] **Step 3: Write minimal documentation**

Add to `README.md` behavior section:

```md
- The first successful scan creates a baseline only and uploads nothing
- Only files created or modified after that baseline are uploaded on later scans
```

Also clarify that configured source directories are scanned at top level only.

- [ ] **Step 4: Run final verification**

Run: `.\.venv\Scripts\python -m pytest -v`
Run: `.\scripts\build_exe.ps1`
Run:

```powershell
Get-Item '.\dist\TftpMonitor\TftpMonitor.exe' | Select-Object FullName,Length,LastWriteTime
```

Expected: tests PASS, build succeeds, and the executable artifact exists.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: describe baseline first sync behavior"
```

## Self-Review

- Spec coverage checked: baseline-only first sync, no backfill on rescan, unchanged files skipped after baseline, and current top-level scan behavior are all covered.
- Placeholder scan checked: all tasks include concrete tests, commands, and expected outcomes.
- Type consistency checked: the plan uses the existing `RemoteFile`, `FileRecord`, `SshSyncService`, and manifest flow without introducing conflicting names.
