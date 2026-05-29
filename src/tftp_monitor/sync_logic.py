from __future__ import annotations

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
