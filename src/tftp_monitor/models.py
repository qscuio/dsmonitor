from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

TransferStatus = Literal["pending", "synced", "failed"]
MonitorDirection = Literal["forward", "reverse"]


@dataclass(slots=True)
class AppSettings:
    source_host: str
    source_user: str
    source_password: str
    source_key_path: str
    source_directories: list[str]
    source_files: list[str]
    destination_host: str
    destination_user: str
    destination_password: str
    destination_key_path: str
    destination_path: str
    reverse_source_files: list[str]
    reverse_destination_path: str
    monitor_direction: MonitorDirection
    poll_interval_seconds: int
    local_cache_dir: Path
    app_data_dir: Path

    @classmethod
    def default(cls, app_data_dir: Path | None = None) -> "AppSettings":
        resolved_dir = app_data_dir or Path.home() / "Documents" / "dsmonitor"
        return cls(
            source_host="10.55.2.104",
            source_user="wei.li",
            source_password="",
            source_key_path="",
            source_directories=[],
            source_files=[],
            destination_host="10.71.1.3",
            destination_user="tsl",
            destination_password="",
            destination_key_path="",
            destination_path="/home/tsl",
            reverse_source_files=[],
            reverse_destination_path="/tftpboot",
            monitor_direction="forward",
            poll_interval_seconds=5,
            local_cache_dir=resolved_dir,
            app_data_dir=resolved_dir,
        )


@dataclass(slots=True)
class FileRecord:
    destination_relative_path: str
    source_directory: str
    source_relative_path: str
    size: int
    modified_time: float
    download_status: TransferStatus
    upload_status: TransferStatus


@dataclass(slots=True)
class RemoteFile:
    source_directory: str
    relative_path: str
    destination_relative_path: str
    size: int
    modified_time: float


@dataclass(slots=True)
class SyncCycleResult:
    scanned_files: int = 0
    changed_files: int = 0
    synced_files: int = 0
    failed_files: int = 0
    last_error: str = ""
    activity: str = "idle"
    current_file: str = ""


@dataclass(slots=True)
class SyncEvent:
    kind: str
    message: str
    activity: str = "idle"
    current_file: str = ""
    bytes_transferred: int = 0
    total_bytes: int = 0
    source_path: str = ""
    source_modified_time: float = 0
    source_size: int = 0
    local_path: str = ""
    local_modified_time: float = 0
    local_size: int = 0
    destination_path: str = ""
    destination_modified_time: float = 0
    destination_size: int = 0


@dataclass(slots=True)
class SyncPlan:
    selected: list[RemoteFile]
    skipped_conflicts: list[RemoteFile]
