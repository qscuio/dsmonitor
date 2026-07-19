from pathlib import Path
from stat import S_IFDIR, S_IFREG
from types import SimpleNamespace

from tftp_monitor.manifest_store import ManifestStore
from tftp_monitor.models import AppSettings, FileRecord, RemoteFile
from tftp_monitor.ssh_sync import (
    SshSyncService,
    _build_auth_options,
    _build_remote_directory_entries,
    _list_remote_directory_with_find,
    parse_glob_monitor_path,
    _scan_remote_file,
    _scan_remote_tree,
    copy_destination_to_source,
    download_manual_file,
    download_target_manual_file,
    upload_manual_file,
    upload_source_manual_file,
)


class FakeMultiSourceGateway:
    def __init__(self, snapshots: dict[str, dict[str, RemoteFile]]) -> None:
        self.snapshots = snapshots
        self.scan_calls: list[str] = []
        self.downloads: list[Path] = []

    def scan(self, source_directory: str) -> dict[str, RemoteFile]:
        self.scan_calls.append(source_directory)
        return self.snapshots[source_directory]

    def download(self, source_directory: str, remote_file: RemoteFile, local_path: Path, progress) -> None:
        assert source_directory == remote_file.source_directory
        self.downloads.append(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"payload")
        progress(7, 7)


class FakeDestinationGateway:
    def __init__(self) -> None:
        self.uploads: list[tuple[Path, str]] = []

    def upload(self, destination_root: str, remote_file: RemoteFile, local_path: Path, progress) -> None:
        self.uploads.append((local_path, destination_root))
        progress(7, 7)

    def stat_file(self, destination_root: str, remote_file: RemoteFile) -> tuple[float, int]:
        return 300.0, 4096


class FakeSftpClient:
    def stat(self, remote_path: str) -> SimpleNamespace:
        assert remote_path == "/tftpboot/image.bin"
        return SimpleNamespace(st_mode=S_IFREG, st_size=42, st_mtime=456.0)

    def listdir_attr(self, root_path: str) -> list[SimpleNamespace]:
        if root_path == "/home/wei.li/nested":
            return [
                SimpleNamespace(filename="a.bin", st_mode=S_IFREG, st_size=5, st_mtime=125.0),
            ]
        assert root_path == "/home/wei.li"
        return [
            SimpleNamespace(filename="2", st_mode=S_IFREG, st_size=4, st_mtime=123.0),
            SimpleNamespace(filename="nested", st_mode=S_IFDIR, st_size=0, st_mtime=124.0),
        ]


class FakeTransferSftpClient:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, str]] = []
        self.put_calls: list[tuple[str, str]] = []
        self.mkdir_calls: list[str] = []
        self.stat_calls: list[str] = []

    def get(self, remote_path: str, local_path: str, callback) -> None:
        self.get_calls.append((remote_path, local_path))
        Path(local_path).write_bytes(b"downloaded")
        callback(10, 10)

    def put(self, local_path: str, remote_path: str, callback) -> None:
        self.put_calls.append((local_path, remote_path))
        callback(7, 7)

    def stat(self, remote_path: str) -> SimpleNamespace:
        self.stat_calls.append(remote_path)
        if remote_path in {"/home", "/home/tsl", "/home/tsl/manual", "/tftpboot", "/tftpboot/manual"}:
            return SimpleNamespace(st_mode=S_IFDIR, st_size=0, st_mtime=0)
        return SimpleNamespace(st_mode=S_IFREG, st_size=10, st_mtime=20)

    def mkdir(self, remote_path: str) -> None:
        self.mkdir_calls.append(remote_path)

    def close(self) -> None:
        pass


class FakeTransferClient:
    def __init__(self, sftp: FakeTransferSftpClient) -> None:
        self.sftp = sftp
        self.closed = False

    def open_sftp(self) -> FakeTransferSftpClient:
        return self.sftp

    def close(self) -> None:
        self.closed = True


class FakeChannel:
    def __init__(self, exit_status: int) -> None:
        self.exit_status = exit_status

    def recv_exit_status(self) -> int:
        return self.exit_status


class FakeCommandStream:
    def __init__(self, payload: bytes, exit_status: int = 0) -> None:
        self.payload = payload
        self.channel = FakeChannel(exit_status)

    def read(self) -> bytes:
        return self.payload


class FakeCommandClient:
    def __init__(self, stdout_payload: bytes, stderr_payload: bytes = b"", exit_status: int = 0) -> None:
        self.stdout_payload = stdout_payload
        self.stderr_payload = stderr_payload
        self.exit_status = exit_status
        self.commands: list[str] = []

    def exec_command(self, command: str):
        self.commands.append(command)
        return (
            None,
            FakeCommandStream(self.stdout_payload, self.exit_status),
            FakeCommandStream(self.stderr_payload),
        )


def test_scan_remote_tree_returns_nested_files() -> None:
    snapshot = _scan_remote_tree(FakeSftpClient(), "/home/wei.li")

    assert list(snapshot) == ["2", "nested/a.bin"]
    assert snapshot["2"].relative_path == "2"
    assert snapshot["2"].source_directory == "/home/wei.li"
    assert snapshot["nested/a.bin"].relative_path == "nested/a.bin"
    assert snapshot["nested/a.bin"].destination_relative_path == "nested/a.bin"


def test_scan_remote_file_returns_only_requested_file() -> None:
    snapshot = _scan_remote_file(FakeSftpClient(), "/tftpboot/image.bin")

    assert list(snapshot) == ["image.bin"]
    assert snapshot["image.bin"].source_directory == "/tftpboot"
    assert snapshot["image.bin"].relative_path == "image.bin"
    assert snapshot["image.bin"].destination_relative_path == "image.bin"


def test_build_remote_directory_entries_sorts_directories_before_files() -> None:
    entries = _build_remote_directory_entries(
        "/tftpboot",
        [
            SimpleNamespace(filename="z.bin", st_mode=S_IFREG, st_size=4, st_mtime=10.0),
            SimpleNamespace(filename="images", st_mode=S_IFDIR, st_size=0, st_mtime=20.0),
        ],
    )

    assert [(entry.name, entry.path, entry.is_directory) for entry in entries] == [
        ("images", "/tftpboot/images", True),
        ("z.bin", "/tftpboot/z.bin", False),
    ]


def test_list_remote_directory_find_fallback_replaces_bad_filename_bytes() -> None:
    payload = (
        b"bad_\xff_name.bin\0"
        b"f\0"
        b"12\0"
        b"1720000000.25\0"
        b"folder\0"
        b"d\0"
        b"0\0"
        b"1720000001.5\0"
    )
    client = FakeCommandClient(payload)

    entries = _list_remote_directory_with_find(client, "/home/tsl/")

    assert client.commands == [
        "LC_ALL=C find /home/tsl -mindepth 1 -maxdepth 1 -printf '%f\\0%y\\0%s\\0%T@\\0'"
    ]
    assert [(entry.name, entry.path, entry.is_directory, entry.size) for entry in entries] == [
        ("folder", "/home/tsl/folder", True, 0),
        ("bad_�_name.bin", "/home/tsl/bad_�_name.bin", False, 12),
    ]


def test_build_auth_options_uses_default_keys_only_without_explicit_credentials() -> None:
    options = _build_auth_options("", "")

    assert options == {
        "password": None,
        "look_for_keys": True,
        "allow_agent": True,
        "timeout": 10,
    }


def test_build_auth_options_supports_password_and_key_file() -> None:
    password_options = _build_auth_options("secret", "")
    key_options = _build_auth_options("", "C:/Users/LiWei/.ssh/id_rsa")

    assert password_options == {
        "password": "secret",
        "look_for_keys": False,
        "allow_agent": False,
        "timeout": 10,
    }
    assert key_options == {
        "password": None,
        "key_filename": "C:/Users/LiWei/.ssh/id_rsa",
        "look_for_keys": False,
        "allow_agent": False,
        "timeout": 10,
    }


def test_manual_download_uses_source_server_and_remote_basename(tmp_path: Path, monkeypatch) -> None:
    settings = AppSettings.default(tmp_path)
    sftp = FakeTransferSftpClient()
    client = FakeTransferClient(sftp)
    calls: list[tuple[str, str, str, str]] = []

    def fake_open_client(host: str, username: str, password: str | None, key_path: str | None):
        calls.append((host, username, password or "", key_path or ""))
        return client

    monkeypatch.setattr("tftp_monitor.ssh_sync._open_client", fake_open_client)

    result = download_manual_file(settings, "/tftpboot/V8888_dev.x", tmp_path / "downloads", lambda _a, _b: None)

    assert calls == [("10.55.2.104", "wei.li", "", "")]
    assert sftp.get_calls == [("/tftpboot/V8888_dev.x", str(tmp_path / "downloads" / "V8888_dev.x"))]
    assert result.local_path == tmp_path / "downloads" / "V8888_dev.x"
    assert result.remote_path == "/tftpboot/V8888_dev.x"
    assert client.closed


def test_manual_download_uses_destination_server_when_requested(tmp_path: Path, monkeypatch) -> None:
    settings = AppSettings.default(tmp_path)
    sftp = FakeTransferSftpClient()
    client = FakeTransferClient(sftp)
    calls: list[tuple[str, str, str, str]] = []

    def fake_open_client(host: str, username: str, password: str | None, key_path: str | None):
        calls.append((host, username, password or "", key_path or ""))
        return client

    monkeypatch.setattr("tftp_monitor.ssh_sync._open_client", fake_open_client)

    result = download_target_manual_file(settings, "/home/tsl/V8888_dev.x", tmp_path / "downloads", lambda _a, _b: None)

    assert calls == [("10.71.1.3", "tsl", "", "")]
    assert sftp.get_calls == [("/home/tsl/V8888_dev.x", str(tmp_path / "downloads" / "V8888_dev.x"))]
    assert result.local_path == tmp_path / "downloads" / "V8888_dev.x"
    assert result.remote_path == "/home/tsl/V8888_dev.x"
    assert client.closed


def test_manual_upload_uses_destination_server_and_local_basename(tmp_path: Path, monkeypatch) -> None:
    settings = AppSettings.default(tmp_path)
    local_file = tmp_path / "V8888_dev.x"
    local_file.write_bytes(b"payload")
    sftp = FakeTransferSftpClient()
    client = FakeTransferClient(sftp)
    calls: list[tuple[str, str, str, str]] = []

    def fake_open_client(host: str, username: str, password: str | None, key_path: str | None):
        calls.append((host, username, password or "", key_path or ""))
        return client

    monkeypatch.setattr("tftp_monitor.ssh_sync._open_client", fake_open_client)

    result = upload_manual_file(settings, local_file, "/home/tsl/manual", lambda _a, _b: None)

    assert calls == [("10.71.1.3", "tsl", "", "")]
    assert sftp.put_calls == [(str(local_file), "/home/tsl/manual/V8888_dev.x")]
    assert result.local_path == local_file
    assert result.remote_path == "/home/tsl/manual/V8888_dev.x"
    assert client.closed


def test_manual_upload_to_source_uses_source_server(tmp_path: Path, monkeypatch) -> None:
    settings = AppSettings.default(tmp_path)
    local_file = tmp_path / "V8888_dev.x"
    local_file.write_bytes(b"payload")
    sftp = FakeTransferSftpClient()
    client = FakeTransferClient(sftp)
    calls: list[tuple[str, str, str, str]] = []

    def fake_open_client(host: str, username: str, password: str | None, key_path: str | None):
        calls.append((host, username, password or "", key_path or ""))
        return client

    monkeypatch.setattr("tftp_monitor.ssh_sync._open_client", fake_open_client)

    result = upload_source_manual_file(settings, local_file, "/tftpboot/manual", lambda _a, _b: None)

    assert calls == [("10.55.2.104", "wei.li", "", "")]
    assert sftp.put_calls == [(str(local_file), "/tftpboot/manual/V8888_dev.x")]
    assert result.local_path == local_file
    assert result.remote_path == "/tftpboot/manual/V8888_dev.x"
    assert client.closed


def test_copy_destination_to_source_downloads_then_uploads_to_source(tmp_path: Path, monkeypatch) -> None:
    settings = AppSettings.default(tmp_path)
    calls: list[tuple[str, str, str, str]] = []
    clients = [
        FakeTransferClient(FakeTransferSftpClient()),
        FakeTransferClient(FakeTransferSftpClient()),
    ]

    def fake_open_client(host: str, username: str, password: str | None, key_path: str | None):
        calls.append((host, username, password or "", key_path or ""))
        return clients.pop(0)

    monkeypatch.setattr("tftp_monitor.ssh_sync._open_client", fake_open_client)

    result = copy_destination_to_source(
        settings,
        "/home/tsl/V8888_dev.x",
        "/tftpboot/manual",
        tmp_path / "transfer",
        lambda _a, _b: None,
    )

    assert calls == [
        ("10.71.1.3", "tsl", "", ""),
        ("10.55.2.104", "wei.li", "", ""),
    ]
    assert result.remote_path == "/tftpboot/manual/V8888_dev.x"


def test_sync_once_scans_only_configured_source_files(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    settings.source_files = ["/tftpboot/image.bin"]
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    manifest_store.save(
        {
            "existing.bin": FileRecord(
                destination_relative_path="existing.bin",
                source_directory="/tftpboot",
                source_relative_path="existing.bin",
                size=1,
                modified_time=1,
                download_status="synced",
                upload_status="synced",
            )
        }
    )
    source = FakeMultiSourceGateway(
        {
            "/tftpboot/image.bin": {
                "image.bin": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="image.bin",
                    destination_relative_path="image.bin",
                    size=42,
                    modified_time=456,
                )
            },
            "/tftpboot": {
                "unwanted.bin": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="unwanted.bin",
                    destination_relative_path="unwanted.bin",
                    size=10,
                    modified_time=20,
                )
            },
        }
    )
    destination = FakeDestinationGateway()
    service = SshSyncService(settings, manifest_store, source, destination)

    result = service.sync_once()

    assert source.scan_calls == ["/tftpboot/image.bin"]
    assert result.scanned_files == 1
    assert result.synced_files == 1
    assert destination.uploads == [(settings.local_cache_dir / "image.bin", "/home/tsl")]


def test_sync_once_supports_glob_monitor_path(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    settings.source_files = ["/tftpboot/*.x"]
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    manifest_store.save(
        {
            "existing.bin": FileRecord(
                destination_relative_path="existing.bin",
                source_directory="/tftpboot",
                source_relative_path="existing.bin",
                size=1,
                modified_time=1,
                download_status="synced",
                upload_status="synced",
            )
        }
    )
    source = FakeMultiSourceGateway(
        {
            "/tftpboot": {
                "V8888_dev.x": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="V8888_dev.x",
                    destination_relative_path="V8888_dev.x",
                    size=42,
                    modified_time=456,
                ),
                "V8500_SFU.6.10.x": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="V8500_SFU.6.10.x",
                    destination_relative_path="V8500_SFU.6.10.x",
                    size=43,
                    modified_time=457,
                ),
                "notes.txt": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="notes.txt",
                    destination_relative_path="notes.txt",
                    size=10,
                    modified_time=20,
                ),
            },
        }
    )
    destination = FakeDestinationGateway()
    service = SshSyncService(settings, manifest_store, source, destination)

    result = service.sync_once()

    assert source.scan_calls == ["/tftpboot"]
    assert result.scanned_files == 2
    assert result.synced_files == 2
    assert set(destination.uploads) == {
        (settings.local_cache_dir / "V8888_dev.x", "/home/tsl"),
        (settings.local_cache_dir / "V8500_SFU.6.10.x", "/home/tsl"),
    }


def test_sync_once_supports_prefix_glob_monitor_path(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    settings.source_files = ["/tftpboot/V8500*"]
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    manifest_store.save(
        {
            "existing.bin": FileRecord(
                destination_relative_path="existing.bin",
                source_directory="/tftpboot",
                source_relative_path="existing.bin",
                size=1,
                modified_time=1,
                download_status="synced",
                upload_status="synced",
            )
        }
    )
    source = FakeMultiSourceGateway(
        {
            "/tftpboot": {
                "V8888_dev.x": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="V8888_dev.x",
                    destination_relative_path="V8888_dev.x",
                    size=42,
                    modified_time=456,
                ),
                "V8500_SFU.6.10.x": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="V8500_SFU.6.10.x",
                    destination_relative_path="V8500_SFU.6.10.x",
                    size=43,
                    modified_time=457,
                ),
            },
        }
    )
    destination = FakeDestinationGateway()
    service = SshSyncService(settings, manifest_store, source, destination)

    result = service.sync_once()

    assert source.scan_calls == ["/tftpboot"]
    assert result.scanned_files == 1
    assert result.synced_files == 1
    assert destination.uploads == [(settings.local_cache_dir / "V8500_SFU.6.10.x", "/home/tsl")]


def test_parse_glob_monitor_path_uses_parent_directory_and_name_pattern() -> None:
    assert parse_glob_monitor_path("/tftpboot/*.x").directory == "/tftpboot"
    assert parse_glob_monitor_path("/tftpboot/*.x").pattern == "*.x"
    assert parse_glob_monitor_path("/tftpboot/V8500*").directory == "/tftpboot"
    assert parse_glob_monitor_path("/tftpboot/V8500*").pattern == "V8500*"
    assert parse_glob_monitor_path("/tftpboot/V8888_dev.x") is None


def test_sync_once_emits_file_state_for_source_local_and_destination(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    settings.source_files = ["/tftpboot/image.bin"]
    local_file = settings.local_cache_dir / "image.bin"
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"cached")
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    manifest_store.save(
        {
            "existing.bin": FileRecord(
                destination_relative_path="existing.bin",
                source_directory="/tftpboot",
                source_relative_path="existing.bin",
                size=1,
                modified_time=1,
                download_status="synced",
                upload_status="synced",
            )
        }
    )
    source = FakeMultiSourceGateway(
        {
            "/tftpboot/image.bin": {
                "image.bin": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="image.bin",
                    destination_relative_path="image.bin",
                    size=42,
                    modified_time=456,
                )
            }
        }
    )
    events = []
    service = SshSyncService(
        settings,
        manifest_store,
        source,
        FakeDestinationGateway(),
        event_callback=events.append,
    )

    service.sync_once()

    file_state = next(event for event in events if event.kind == "file_state")
    assert file_state.source_path == "/tftpboot/image.bin"
    assert file_state.source_modified_time == 456
    assert file_state.source_size == 42
    assert file_state.local_path == str(local_file)
    assert file_state.local_modified_time > 0
    assert file_state.local_size == len(b"payload")
    assert file_state.destination_path == "/home/tsl/image.bin"
    assert file_state.destination_modified_time == 300
    assert file_state.destination_size == 4096


def test_sync_once_logs_conflicts_and_uploads_only_newest_file(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    settings.source_directories = ["/tftpboot", "/home/wei.li"]
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    manifest_store.save(
        {
            "older.bin": FileRecord(
                destination_relative_path="older.bin",
                source_directory="/tftpboot",
                source_relative_path="older.bin",
                size=1,
                modified_time=1,
                download_status="synced",
                upload_status="synced",
            )
        }
    )
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


def test_first_sync_creates_baseline_without_upload(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    settings.source_directories = ["/tftpboot", "/home/wei.li"]
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
    assert (settings.local_cache_dir / "existing.bin").read_bytes() == b"payload"
    assert destination.uploads == []
    assert manifest_store.load()["existing.bin"].upload_status == "synced"
    assert any("Baseline created" in message for message in events)


def test_synced_manifest_repairs_missing_local_cache_without_upload(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    settings.source_files = ["/tftpboot/image.bin"]
    manifest_store = ManifestStore(app_data_dir / "manifest.json")
    manifest_store.save(
        {
            "image.bin": FileRecord(
                destination_relative_path="image.bin",
                source_directory="/tftpboot",
                source_relative_path="image.bin",
                size=42,
                modified_time=456,
                download_status="synced",
                upload_status="synced",
            )
        }
    )
    source = FakeMultiSourceGateway(
        {
            "/tftpboot/image.bin": {
                "image.bin": RemoteFile(
                    source_directory="/tftpboot",
                    relative_path="image.bin",
                    destination_relative_path="image.bin",
                    size=42,
                    modified_time=456,
                )
            }
        }
    )
    destination = FakeDestinationGateway()
    service = SshSyncService(settings, manifest_store, source, destination)

    result = service.sync_once()

    assert result.changed_files == 0
    assert result.synced_files == 0
    assert (settings.local_cache_dir / "image.bin").read_bytes() == b"payload"
    assert source.downloads == [settings.local_cache_dir / "image.bin"]
    assert destination.uploads == []


def test_second_sync_after_baseline_uploads_only_new_file(tmp_path: Path) -> None:
    app_data_dir = tmp_path / "appdata"
    settings = AppSettings.default(app_data_dir)
    settings.source_directories = ["/tftpboot", "/home/wei.li"]
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
