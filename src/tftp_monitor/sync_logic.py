from __future__ import annotations

from .models import FileRecord, RemoteFile, SyncPlan


def build_sync_plan(
    remote_files: list[RemoteFile],
    manifest: dict[str, FileRecord],
) -> SyncPlan:
    winners: dict[str, RemoteFile] = {}
    skipped_conflicts: list[RemoteFile] = []

    for remote in sorted(
        remote_files,
        key=lambda item: (item.destination_relative_path, item.modified_time, item.source_directory),
    ):
        existing = winners.get(remote.destination_relative_path)
        if existing is None:
            winners[remote.destination_relative_path] = remote
            continue
        if remote.modified_time >= existing.modified_time:
            skipped_conflicts.append(existing)
            winners[remote.destination_relative_path] = remote
        else:
            skipped_conflicts.append(remote)

    selected: list[RemoteFile] = []
    for destination_relative_path, remote in sorted(winners.items()):
        current = manifest.get(destination_relative_path)
        if current is None:
            selected.append(remote)
            continue
        if current.source_directory != remote.source_directory:
            selected.append(remote)
            continue
        if current.source_relative_path != remote.relative_path:
            selected.append(remote)
            continue
        if current.size != remote.size or current.modified_time != remote.modified_time:
            selected.append(remote)
            continue
        if current.download_status != "synced" or current.upload_status != "synced":
            selected.append(remote)

    return SyncPlan(selected=selected, skipped_conflicts=skipped_conflicts)


def apply_successful_sync(
    manifest: dict[str, FileRecord],
    remote_file: RemoteFile,
) -> None:
    manifest[remote_file.destination_relative_path] = FileRecord(
        destination_relative_path=remote_file.destination_relative_path,
        source_directory=remote_file.source_directory,
        source_relative_path=remote_file.relative_path,
        size=remote_file.size,
        modified_time=remote_file.modified_time,
        download_status="synced",
        upload_status="synced",
    )
