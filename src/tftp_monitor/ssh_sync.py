from __future__ import annotations

import fnmatch
import shlex
from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(slots=True)
class RemoteDirectoryEntry:
    name: str
    path: str
    is_directory: bool
    size: int
    modified_time: float


@dataclass(slots=True)
class ManualTransferResult:
    local_path: Path
    remote_path: str


@dataclass(slots=True, frozen=True)
class GlobMonitorPath:
    directory: str
    pattern: str


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

    def stat_file(self, destination_root: str, remote_file: RemoteFile) -> tuple[float, int]:
        ...


class ParamikoSourceGateway:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def scan(self, source_path: str) -> dict[str, RemoteFile]:
        client = _open_client(
            self.settings.source_host,
            self.settings.source_user,
            self.settings.source_password,
            self.settings.source_key_path,
        )
        sftp = client.open_sftp()
        try:
            attrs = sftp.stat(source_path)
            if S_ISDIR(attrs.st_mode):
                return _scan_remote_tree(sftp, source_path)
            if S_ISREG(attrs.st_mode):
                return _scan_remote_file(sftp, source_path)
            return {}
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
        client = _open_client(
            self.settings.source_host,
            self.settings.source_user,
            self.settings.source_password,
            self.settings.source_key_path,
        )
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
            self.settings.destination_key_path,
        )
        sftp = client.open_sftp()
        try:
            remote_path = build_destination_path(destination_root, remote_file.destination_relative_path)
            _ensure_remote_directories(sftp, str(PurePosixPath(remote_path).parent))
            sftp.put(str(local_path), remote_path, callback=progress)
        finally:
            sftp.close()
            client.close()

    def stat_file(self, destination_root: str, remote_file: RemoteFile) -> tuple[float, int]:
        client = _open_client(
            self.settings.destination_host,
            self.settings.destination_user,
            self.settings.destination_password,
            self.settings.destination_key_path,
        )
        sftp = client.open_sftp()
        try:
            remote_path = build_destination_path(destination_root, remote_file.destination_relative_path)
            try:
                attrs = sftp.stat(remote_path)
                return float(attrs.st_mtime), int(attrs.st_size)
            except OSError:
                return 0, 0
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
        source_paths = self.settings.source_files or self.settings.source_directories
        for source_path in source_paths:
            glob_path = parse_glob_monitor_path(source_path)
            scan_path = glob_path.directory if glob_path is not None else source_path
            self._emit(
                "scan_directory",
                f"Scanning {source_path}",
                activity="scanning",
            )
            scanned_files = self.source_gateway.scan(scan_path)
            if glob_path is not None:
                scanned_files = filter_remote_files_by_glob(scanned_files, glob_path.pattern)
            remote_files.extend(scanned_files.values())
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
        is_first_sync = not manifest
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

        if is_first_sync:
            for remote_file in plan.selected:
                self._download_local_cache(remote_file)
                apply_successful_sync(manifest, remote_file)
                self._emit_file_state(remote_file)
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

        selected_files = {
            (remote_file.source_directory, remote_file.relative_path, remote_file.destination_relative_path)
            for remote_file in plan.selected
        }
        for remote_file in remote_files:
            selected_key = (
                remote_file.source_directory,
                remote_file.relative_path,
                remote_file.destination_relative_path,
            )
            if selected_key in selected_files:
                continue
            if self._should_repair_local_cache(manifest, remote_file):
                self._download_local_cache(remote_file)
            self._emit_file_state(remote_file)

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
                self._emit_file_state(remote_file)
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

    def _download_local_cache(self, remote_file: RemoteFile) -> Path:
        local_path = build_local_cache_path(
            self.settings.local_cache_dir,
            remote_file.destination_relative_path,
        )
        self.download_file(remote_file, local_path, lambda _done, _total: None)
        return local_path

    def _should_repair_local_cache(
        self,
        manifest: dict[str, FileRecord],
        remote_file: RemoteFile,
    ) -> bool:
        local_path = build_local_cache_path(
            self.settings.local_cache_dir,
            remote_file.destination_relative_path,
        )
        if local_path.exists():
            return False
        current = manifest.get(remote_file.destination_relative_path)
        if current is None:
            return False
        return (
            current.source_directory == remote_file.source_directory
            and current.source_relative_path == remote_file.relative_path
            and current.size == remote_file.size
            and current.modified_time == remote_file.modified_time
            and current.download_status == "synced"
            and current.upload_status == "synced"
        )

    def _emit_file_state(self, remote_file: RemoteFile) -> None:
        local_path = build_local_cache_path(
            self.settings.local_cache_dir,
            remote_file.destination_relative_path,
        )
        local_stat = local_path.stat() if local_path.exists() else None
        local_modified_time = local_stat.st_mtime if local_stat else 0
        local_size = local_stat.st_size if local_stat else 0
        try:
            destination_modified_time, destination_size = self.destination_gateway.stat_file(
                self.settings.destination_path,
                remote_file,
            )
        except Exception:
            destination_modified_time = 0
            destination_size = 0
        self._emit(
            "file_state",
            "File state refreshed",
            source_path=build_destination_path(remote_file.source_directory, remote_file.relative_path),
            source_modified_time=remote_file.modified_time,
            source_size=remote_file.size,
            local_path=str(local_path),
            local_modified_time=local_modified_time,
            local_size=local_size,
            destination_path=build_destination_path(
                self.settings.destination_path,
                remote_file.destination_relative_path,
            ),
            destination_modified_time=destination_modified_time,
            destination_size=destination_size,
        )

    def _emit(
        self,
        kind: str,
        message: str,
        *,
        activity: str = "idle",
        current_file: str = "",
        bytes_transferred: int = 0,
        total_bytes: int = 0,
        source_path: str = "",
        source_modified_time: float = 0,
        source_size: int = 0,
        local_path: str = "",
        local_modified_time: float = 0,
        local_size: int = 0,
        destination_path: str = "",
        destination_modified_time: float = 0,
        destination_size: int = 0,
    ) -> None:
        self.event_callback(
            SyncEvent(
                kind=kind,
                message=message,
                activity=activity,
                current_file=current_file,
                bytes_transferred=bytes_transferred,
                total_bytes=total_bytes,
                source_path=source_path,
                source_modified_time=source_modified_time,
                source_size=source_size,
                local_path=local_path,
                local_modified_time=local_modified_time,
                local_size=local_size,
                destination_path=destination_path,
                destination_modified_time=destination_modified_time,
                destination_size=destination_size,
            )
        )


def _build_auth_options(password: str | None, key_path: str | None) -> dict[str, object]:
    effective_password = password or None
    effective_key_path = key_path or None
    use_default_keys = effective_password is None and effective_key_path is None
    options: dict[str, object] = {
        "password": effective_password,
        "look_for_keys": use_default_keys,
        "allow_agent": use_default_keys,
        "timeout": 10,
    }
    if effective_key_path:
        options["key_filename"] = effective_key_path
    return options


def _open_client(
    host: str,
    username: str,
    password: str | None,
    key_path: str | None,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=username,
        **_build_auth_options(password, key_path),
    )
    return client


def _scan_remote_file(sftp: paramiko.SFTPClient, source_file: str) -> dict[str, RemoteFile]:
    attrs = sftp.stat(source_file)
    if not S_ISREG(attrs.st_mode):
        return {}
    remote_path = PurePosixPath(source_file)
    source_directory = str(remote_path.parent)
    relative_path = remote_path.name
    return {
        relative_path: RemoteFile(
            source_directory=source_directory,
            relative_path=relative_path,
            destination_relative_path=relative_path,
            size=attrs.st_size,
            modified_time=float(attrs.st_mtime),
        )
    }


def parse_glob_monitor_path(source_path: str) -> GlobMonitorPath | None:
    path = PurePosixPath(source_path.strip())
    name_pattern = path.name
    if not any(marker in name_pattern for marker in ("*", "?", "[")):
        return None
    parent = str(path.parent)
    if not parent or parent == ".":
        parent = "/"
    return GlobMonitorPath(directory=parent, pattern=name_pattern)


def filter_remote_files_by_glob(
    remote_files: dict[str, RemoteFile],
    pattern: str,
) -> dict[str, RemoteFile]:
    return {
        key: remote_file
        for key, remote_file in remote_files.items()
        if fnmatch.fnmatchcase(PurePosixPath(remote_file.relative_path).name, pattern)
        or fnmatch.fnmatchcase(remote_file.relative_path, pattern)
    }


def _scan_remote_tree(sftp: paramiko.SFTPClient, root_path: str) -> dict[str, RemoteFile]:
    snapshot: dict[str, RemoteFile] = {}
    _scan_remote_tree_into(sftp, PurePosixPath(root_path), PurePosixPath("."), snapshot)
    return snapshot


def _scan_remote_tree_into(
    sftp: paramiko.SFTPClient,
    root_path: PurePosixPath,
    relative_directory: PurePosixPath,
    snapshot: dict[str, RemoteFile],
) -> None:
    current_directory = root_path if str(relative_directory) == "." else root_path.joinpath(relative_directory)
    for entry in sftp.listdir_attr(str(current_directory)):
        entry_relative = (
            PurePosixPath(entry.filename)
            if str(relative_directory) == "."
            else relative_directory.joinpath(entry.filename)
        )
        entry_remote_path = root_path.joinpath(entry_relative)
        if S_ISREG(entry.st_mode):
            relative_path = str(entry_relative)
            snapshot[relative_path] = RemoteFile(
                source_directory=str(root_path),
                relative_path=relative_path,
                destination_relative_path=relative_path,
                size=entry.st_size,
                modified_time=float(entry.st_mtime),
            )
        elif S_ISDIR(entry.st_mode):
            _scan_remote_tree_into(sftp, root_path, entry_relative, snapshot)


def list_remote_directory(
    host: str,
    username: str,
    password: str | None,
    key_path: str | None,
    directory: str,
) -> list[RemoteDirectoryEntry]:
    client = _open_client(host, username, password, key_path)
    sftp = client.open_sftp()
    try:
        try:
            return _build_remote_directory_entries(directory, sftp.listdir_attr(directory))
        except UnicodeDecodeError:
            sftp.close()
            return _list_remote_directory_with_find(client, directory)
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        client.close()


def _list_remote_directory_with_find(client: paramiko.SSHClient, directory: str) -> list[RemoteDirectoryEntry]:
    normalized_directory = directory.rstrip("/") or "/"
    quoted_directory = shlex.quote(normalized_directory)
    command = f"LC_ALL=C find {quoted_directory} -mindepth 1 -maxdepth 1 -printf '%f\\0%y\\0%s\\0%T@\\0'"
    _stdin, stdout, stderr = client.exec_command(command)
    output = stdout.read()
    error = stderr.read()
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        message = error.decode("utf-8", errors="replace").strip() or f"find exited with {exit_status}"
        raise FileNotFoundError(message)

    parts = output.split(b"\0")
    entries: list[RemoteDirectoryEntry] = []
    root = PurePosixPath(normalized_directory)
    for index in range(0, len(parts) - 1, 4):
        if index + 3 >= len(parts) or not parts[index]:
            continue
        name = parts[index].decode("utf-8", errors="replace")
        file_type = parts[index + 1].decode("ascii", errors="ignore")
        size_text = parts[index + 2].decode("ascii", errors="ignore")
        modified_text = parts[index + 3].decode("ascii", errors="ignore")
        is_directory = file_type == "d"
        if file_type not in {"d", "f"}:
            continue
        entries.append(
            RemoteDirectoryEntry(
                name=name,
                path=str(root.joinpath(name)),
                is_directory=is_directory,
                size=int(size_text or "0"),
                modified_time=float(modified_text or "0"),
            )
        )
    return sorted(entries, key=lambda item: (not item.is_directory, item.name.lower()))


def download_manual_file(
    settings: AppSettings,
    remote_path: str,
    local_directory: Path,
    progress: ProgressCallback,
) -> ManualTransferResult:
    remote_file = PurePosixPath(remote_path)
    if not remote_file.name:
        raise ValueError("Remote file path must include a filename.")
    local_directory.mkdir(parents=True, exist_ok=True)
    local_path = local_directory / remote_file.name
    client = _open_client(
        settings.source_host,
        settings.source_user,
        settings.source_password,
        settings.source_key_path,
    )
    sftp = client.open_sftp()
    try:
        sftp.get(str(remote_file), str(local_path), callback=progress)
        return ManualTransferResult(local_path=local_path, remote_path=str(remote_file))
    finally:
        sftp.close()
        client.close()


def download_target_manual_file(
    settings: AppSettings,
    remote_path: str,
    local_directory: Path,
    progress: ProgressCallback,
) -> ManualTransferResult:
    remote_file = PurePosixPath(remote_path)
    if not remote_file.name:
        raise ValueError("Remote file path must include a filename.")
    local_directory.mkdir(parents=True, exist_ok=True)
    local_path = local_directory / remote_file.name
    client = _open_client(
        settings.destination_host,
        settings.destination_user,
        settings.destination_password,
        settings.destination_key_path,
    )
    sftp = client.open_sftp()
    try:
        sftp.get(str(remote_file), str(local_path), callback=progress)
        return ManualTransferResult(local_path=local_path, remote_path=str(remote_file))
    finally:
        sftp.close()
        client.close()


def upload_manual_file(
    settings: AppSettings,
    local_path: Path,
    remote_directory: str,
    progress: ProgressCallback,
) -> ManualTransferResult:
    if not local_path.is_file():
        raise ValueError(f"Local file does not exist: {local_path}")
    remote_path = str(PurePosixPath(remote_directory).joinpath(local_path.name))
    client = _open_client(
        settings.destination_host,
        settings.destination_user,
        settings.destination_password,
        settings.destination_key_path,
    )
    sftp = client.open_sftp()
    try:
        _ensure_remote_directories(sftp, str(PurePosixPath(remote_path).parent))
        sftp.put(str(local_path), remote_path, callback=progress)
        return ManualTransferResult(local_path=local_path, remote_path=remote_path)
    finally:
        sftp.close()
        client.close()


def upload_source_manual_file(
    settings: AppSettings,
    local_path: Path,
    remote_directory: str,
    progress: ProgressCallback,
) -> ManualTransferResult:
    if not local_path.is_file():
        raise ValueError(f"Local file does not exist: {local_path}")
    remote_path = str(PurePosixPath(remote_directory).joinpath(local_path.name))
    client = _open_client(
        settings.source_host,
        settings.source_user,
        settings.source_password,
        settings.source_key_path,
    )
    sftp = client.open_sftp()
    try:
        _ensure_remote_directories(sftp, str(PurePosixPath(remote_path).parent))
        sftp.put(str(local_path), remote_path, callback=progress)
        return ManualTransferResult(local_path=local_path, remote_path=remote_path)
    finally:
        sftp.close()
        client.close()


def copy_source_to_destination(
    settings: AppSettings,
    source_remote_path: str,
    destination_directory: str,
    temp_directory: Path,
    progress: ProgressCallback,
) -> ManualTransferResult:
    downloaded = download_manual_file(settings, source_remote_path, temp_directory, progress)
    return upload_manual_file(settings, downloaded.local_path, destination_directory, progress)


def copy_destination_to_source(
    settings: AppSettings,
    destination_remote_path: str,
    source_directory: str,
    temp_directory: Path,
    progress: ProgressCallback,
) -> ManualTransferResult:
    downloaded = download_target_manual_file(settings, destination_remote_path, temp_directory, progress)
    return upload_source_manual_file(settings, downloaded.local_path, source_directory, progress)


def _build_remote_directory_entries(directory: str, attrs: list[object]) -> list[RemoteDirectoryEntry]:
    entries: list[RemoteDirectoryEntry] = []
    root = PurePosixPath(directory)
    for entry in attrs:
        name = entry.filename
        is_directory = S_ISDIR(entry.st_mode)
        if not is_directory and not S_ISREG(entry.st_mode):
            continue
        entries.append(
            RemoteDirectoryEntry(
                name=name,
                path=str(root.joinpath(name)),
                is_directory=is_directory,
                size=entry.st_size,
                modified_time=float(entry.st_mtime),
            )
        )
    return sorted(entries, key=lambda item: (not item.is_directory, item.name.lower()))


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
