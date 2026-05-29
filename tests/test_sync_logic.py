from tftp_monitor.models import FileRecord, RemoteFile
from tftp_monitor.sync_logic import apply_successful_sync, select_files_to_sync


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


def test_apply_successful_sync_marks_file_as_synced() -> None:
    manifest: dict[str, FileRecord] = {}
    remote = RemoteFile(relative_path="configs/a.bin", size=15, modified_time=30)

    apply_successful_sync(manifest, remote)

    record = manifest["configs/a.bin"]
    assert record.size == 15
    assert record.download_status == "synced"
    assert record.upload_status == "synced"
