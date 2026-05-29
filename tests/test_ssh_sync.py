from pathlib import Path

from tftp_monitor.manifest_store import ManifestStore
from tftp_monitor.models import AppSettings, RemoteFile
from tftp_monitor.ssh_sync import SshSyncService


class FakeMultiSourceGateway:
    def __init__(self, snapshots: dict[str, dict[str, RemoteFile]]) -> None:
        self.snapshots = snapshots

    def scan(self, source_directory: str) -> dict[str, RemoteFile]:
        return self.snapshots[source_directory]

    def download(self, source_directory: str, remote_file: RemoteFile, local_path: Path, progress) -> None:
        assert source_directory == remote_file.source_directory
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"payload")
        progress(7, 7)


class FakeDestinationGateway:
    def __init__(self) -> None:
        self.uploads: list[tuple[Path, str]] = []

    def upload(self, destination_root: str, remote_file: RemoteFile, local_path: Path, progress) -> None:
        self.uploads.append((local_path, destination_root))
        progress(7, 7)


def test_sync_once_logs_conflicts_and_uploads_only_newest_file(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    events: list[str] = []
    source = FakeMultiSourceGateway(
        {
            "/tftpboot": {
                "fw/a.bin": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="fw/a.bin",
                    destination_relative_path="fw/a.bin",
                    size=10,
                    modified_time=20,
                )
            },
            "/home/wei.li": {
                "fw/a.bin": RemoteFile(
                    source_directory="/home/wei.li",
                    relative_path="fw/a.bin",
                    destination_relative_path="fw/a.bin",
                    size=11,
                    modified_time=30,
                )
            },
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

    assert result.scanned_files == 2
    assert result.changed_files == 1
    assert result.synced_files == 1
    assert result.failed_files == 0
    assert (settings.local_cache_dir / "fw" / "a.bin").read_bytes() == b"payload"
    assert destination.uploads == [
        (settings.local_cache_dir / "fw" / "a.bin", "/home/tsl")
    ]
    manifest = manifest_store.load()
    assert manifest["fw/a.bin"].upload_status == "synced"
    assert manifest["fw/a.bin"].source_directory == "/home/wei.li"
    assert any("Conflict on fw/a.bin" in message for message in events)
    assert events[-1] == "Sync cycle completed"
