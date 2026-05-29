from pathlib import Path

from tftp_monitor.manifest_store import ManifestStore
from tftp_monitor.models import AppSettings, RemoteFile
from tftp_monitor.ssh_sync import SshSyncService


class FakeSourceGateway:
    def __init__(self) -> None:
        self.snapshot = {
            "configs/a.bin": RemoteFile(
                relative_path="configs/a.bin",
                size=7,
                modified_time=123.0,
            )
        }

    def scan(self, root_path: str) -> dict[str, RemoteFile]:
        assert root_path == "/home/wei.li"
        return self.snapshot

    def download(self, root_path: str, remote_file: RemoteFile, local_path: Path, progress) -> None:
        assert root_path == "/home/wei.li"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"payload")
        progress(7, 7)


class FakeDestinationGateway:
    def __init__(self) -> None:
        self.uploads: list[tuple[Path, str]] = []

    def upload(self, destination_root: str, remote_file: RemoteFile, local_path: Path, progress) -> None:
        self.uploads.append((local_path, destination_root))
        progress(7, 7)


def test_sync_once_downloads_uploads_and_updates_manifest(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    events: list[str] = []
    source = FakeSourceGateway()
    destination = FakeDestinationGateway()
    service = SshSyncService(
        settings=settings,
        manifest_store=manifest_store,
        source_gateway=source,
        destination_gateway=destination,
        event_callback=lambda event: events.append(event.kind),
    )

    result = service.sync_once()

    assert result.scanned_files == 1
    assert result.changed_files == 1
    assert result.synced_files == 1
    assert result.failed_files == 0
    assert (settings.local_cache_dir / "configs" / "a.bin").read_bytes() == b"payload"
    assert destination.uploads == [
        (settings.local_cache_dir / "configs" / "a.bin", "/home/tsl")
    ]
    manifest = manifest_store.load()
    assert manifest["configs/a.bin"].upload_status == "synced"
    assert events[0] == "scan_started"
    assert events[-1] == "cycle_completed"
