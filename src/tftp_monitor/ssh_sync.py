from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG
from typing import Protocol

import paramiko

from .manifest_store import ManifestStore
from .models import AppSettings, FileRecord, RemoteFile, SyncCycleResult, SyncEvent
from .pathing import build_destination_path, build_local_cache_path
from .sync_logic import apply_successful_sync, build_sync_plan

ProgressCallback = Callable[[int, int], None]
EventCallback = Callable[[SyncEvent], None]


class SourceGateway(Protocol):
    def scan(self, source_directory: str) -> dict[str, RemoteFile]:
        ...

    def download(
        self,
        source_directory: str,
        remote_file: RemoteFile,
        local_path: Path,
        progress: ProgressCallback,
    ) -> None:
        ...


class DestinationGateway(Protocol):
    def upload(
        self,
        destination_root: str,
        remote_file: RemoteFile,
        local_path: Path,
        progress: ProgressCallback,
    ) -> None:
        ...


class ParamikoSourceGateway:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def scan(self, source_directory: str) -> dict[str, RemoteFile]:
        client = _open_client(self.settings.source_host, self.settings.source_user, None)
        sftp = client.open_sftp()
        try:
            return _scan_remote_tree(sftp, source_directory)
        finally:
            sftp.close()
            client.close()

    def download(
        self,
        source_directory: str,
        remote_file: RemoteFile,
        local_path: Path,
        progress: ProgressCallback,
    ) -> None:
        client = _open_client(self.settings.source_host, self.settings.source_user, None)
        sftp = client.open_sftp()
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            remote_path = build_destination_path(source_directory, remote_file.relative_path)
            sftp.get(remote_path, str(local_path), callback=progress)
        finally:
            sftp.close()
            client.close()


class ParamikoDestinationGateway:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def upload(
        self,
        destination_root: str,
        remote_file: RemoteFile,
        local_path: Path,
        progress: ProgressCallback,
    ) -> None:
        client = _open_client(
            self.settings.destination_host,
            self.settings.destination_user,
            self.settings.destination_password,
        )
        sftp = client.open_sftp()
        try:
            remote_path = build_destination_path(destination_root, remote_file.destination_relative_path)
            _ensure_remote_directories(sftp, str(PurePosixPath(remote_path).parent))
            sftp.put(str(local_path), remote_path, callback=progress)
        finally:
            sftp.close()
            client.close()


class SshSyncService:
    def __init__(
        self,
        settings: AppSettings,
        manifest_store: ManifestStore,
        source_gateway: SourceGateway | None = None,
        destination_gateway: DestinationGateway | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.settings = settings
        self.manifest_store = manifest_store
        self.source_gateway = source_gateway or ParamikoSourceGateway(settings)
        self.destination_gateway = destination_gateway or ParamikoDestinationGateway(settings)
        self.event_callback = event_callback or (lambda event: None)

    def scan_source(self) -> list[RemoteFile]:
        remote_files: list[RemoteFile] = []
        for source_directory in self.settings.source_directories:
            self._emit(
                "scan_directory",
                f"Scanning {source_directory}",
                activity="scanning",
            )
            remote_files.extend(self.source_gateway.scan(source_directory).values())
        return remote_files

    def download_file(
        self,
        remote_file: RemoteFile,
        local_path: Path,
        progress: ProgressCallback,
    ) -> None:
        self.source_gateway.download(remote_file.source_directory, remote_file, local_path, progress)

    def upload_file(
        self,
        local_path: Path,
        remote_file: RemoteFile,
        progress: ProgressCallback,
    ) -> None:
        self.destination_gateway.upload(
            self.settings.destination_path,
            remote_file,
            local_path,
            progress,
        )

    def sync_once(self) -> SyncCycleResult:
        manifest = self.manifest_store.load()
        result = SyncCycleResult(activity="scanning")
        self._emit("scan_started", "Scanning source tree", activity="scanning")

        try:
            remote_files = self.scan_source()
        except Exception as exc:
            result.failed_files = 1
            result.last_error = str(exc)
            result.activity = "error"
            self._emit("cycle_completed", f"Scan failed: {exc}", activity="error")
            return result

        result.scanned_files = len(remote_files)
        plan = build_sync_plan(remote_files, manifest)
        result.changed_files = len(plan.selected)

        for skipped in plan.skipped_conflicts:
            winner = next(
                item for item in plan.selected
                if item.destination_relative_path == skipped.destination_relative_path
            )
            self._emit(
                "file_conflict",
                (
                    f"Conflict on {skipped.destination_relative_path}: "
                    f"chose {winner.source_directory}/{winner.relative_path} "
                    f"over {skipped.source_directory}/{skipped.relative_path}"
                ),
                activity="scanning",
                current_file=skipped.destination_relative_path,
            )

        for remote_file in plan.selected:
            local_path = build_local_cache_path(
                self.settings.local_cache_dir,
                remote_file.destination_relative_path,
            )
            result.current_file = remote_file.destination_relative_path
            downloaded = False
            try:
                self._emit(
                    "file_changed",
                    f"Syncing {remote_file.destination_relative_path}",
                    activity="downloading",
                    current_file=remote_file.destination_relative_path,
                )
                self.download_file(
                    remote_file,
                    local_path,
                    lambda done, total: self._emit(
                        "download_progress",
                        f"Downloading {remote_file.destination_relative_path}",
                        activity="downloading",
                        current_file=remote_file.destination_relative_path,
                        bytes_transferred=done,
                        total_bytes=total,
                    ),
                )
                downloaded = True
                self.upload_file(
                    local_path,
                    remote_file,
                    lambda done, total: self._emit(
                        "upload_progress",
                        f"Uploading {remote_file.destination_relative_path}",
                        activity="uploading",
                        current_file=remote_file.destination_relative_path,
                        bytes_transferred=done,
                        total_bytes=total,
                    ),
                )
                apply_successful_sync(manifest, remote_file)
                self.manifest_store.save(manifest)
                result.synced_files += 1
                self._emit(
                    "file_synced",
                    f"Synced {remote_file.destination_relative_path}",
                    activity="idle",
                    current_file=remote_file.destination_relative_path,
                )
            except Exception as exc:
                result.failed_files += 1
                result.last_error = str(exc)
                manifest[remote_file.destination_relative_path] = FileRecord(
                    destination_relative_path=remote_file.destination_relative_path,
                    source_directory=remote_file.source_directory,
                    source_relative_path=remote_file.relative_path,
                    size=remote_file.size,
                    modified_time=remote_file.modified_time,
                    download_status="synced" if downloaded else "failed",
                    upload_status="failed",
                )
                self.manifest_store.save(manifest)
                self._emit(
                    "file_failed",
                    f"Failed to sync {remote_file.destination_relative_path}: {exc}",
                    activity="error",
                    current_file=remote_file.destination_relative_path,
                )

        result.activity = "idle" if result.failed_files == 0 else "error"
        self._emit(
            "cycle_completed",
            "Sync cycle completed",
            activity=result.activity,
            current_file=result.current_file,
        )
        return result

    def _emit(
        self,
        kind: str,
        message: str,
        *,
        activity: str = "idle",
        current_file: str = "",
        bytes_transferred: int = 0,
        total_bytes: int = 0,
    ) -> None:
        self.event_callback(
            SyncEvent(
                kind=kind,
                message=message,
                activity=activity,
                current_file=current_file,
                bytes_transferred=bytes_transferred,
                total_bytes=total_bytes,
            )
        )


def _open_client(host: str, username: str, password: str | None) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=username,
        password=password,
        look_for_keys=password is None,
        allow_agent=password is None,
        timeout=10,
    )
    return client


def _scan_remote_tree(sftp: paramiko.SFTPClient, root_path: str) -> dict[str, RemoteFile]:
    snapshot: dict[str, RemoteFile] = {}
    stack: list[tuple[str, str]] = [(root_path, "")]
    while stack:
        current_path, relative_prefix = stack.pop()
        for entry in sftp.listdir_attr(current_path):
            entry_relative = f"{relative_prefix}/{entry.filename}".strip("/")
            entry_path = str(PurePosixPath(current_path).joinpath(entry.filename))
            if S_ISDIR(entry.st_mode):
                stack.append((entry_path, entry_relative))
            elif S_ISREG(entry.st_mode):
                snapshot[entry_relative] = RemoteFile(
                    source_directory=root_path,
                    relative_path=entry_relative,
                    destination_relative_path=entry_relative,
                    size=entry.st_size,
                    modified_time=float(entry.st_mtime),
                )
    return snapshot


def _ensure_remote_directories(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    current = PurePosixPath("/")
    for part in PurePosixPath(remote_dir).parts:
        if part == "/":
            continue
        current = current.joinpath(part)
        try:
            sftp.stat(str(current))
        except OSError:
            sftp.mkdir(str(current))
