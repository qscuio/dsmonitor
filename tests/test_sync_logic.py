from tftp_monitor.models import FileRecord, RemoteFile
from tftp_monitor.sync_logic import apply_successful_sync, build_sync_plan


def test_build_sync_plan_keeps_newest_conflicting_file() -> None:
    remote_files = [
        RemoteFile(
            source_directory="/tftpboot",
            relative_path="fw/a.bin",
            destination_relative_path="fw/a.bin",
            size=9,
            modified_time=20,
        ),
        RemoteFile(
            source_directory="/home/wei.li",
            relative_path="fw/a.bin",
            destination_relative_path="fw/a.bin",
            size=11,
            modified_time=30,
        ),
    ]

    plan = build_sync_plan(remote_files, {})

    assert len(plan.selected) == 1
    assert plan.selected[0].source_directory == "/home/wei.li"
    assert plan.selected[0].destination_relative_path == "fw/a.bin"
    assert len(plan.skipped_conflicts) == 1
    assert plan.skipped_conflicts[0].source_directory == "/tftpboot"


def test_build_sync_plan_respects_manifest_for_unchanged_winner() -> None:
    remote_files = [
        RemoteFile(
            source_directory="/home/wei.li",
            relative_path="fw/a.bin",
            destination_relative_path="fw/a.bin",
            size=11,
            modified_time=30,
        )
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
    assert plan.skipped_conflicts == []


def test_apply_successful_sync_marks_file_as_synced() -> None:
    manifest: dict[str, FileRecord] = {}
    remote = RemoteFile(
        source_directory="/home/wei.li",
        relative_path="configs/a.bin",
        destination_relative_path="configs/a.bin",
        size=15,
        modified_time=30,
    )

    apply_successful_sync(manifest, remote)

    record = manifest["configs/a.bin"]
    assert record.size == 15
    assert record.source_directory == "/home/wei.li"
    assert record.download_status == "synced"
    assert record.upload_status == "synced"
