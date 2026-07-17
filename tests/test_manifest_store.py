from pathlib import Path

from tftp_monitor.manifest_store import ManifestStore
from tftp_monitor.models import AppSettings, FileRecord
from tftp_monitor.settings_store import SettingsStore


def test_manifest_store_round_trips_records(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.json")
    record = FileRecord(
        destination_relative_path="images/fw.bin",
        source_directory="/tftpboot",
        source_relative_path="images/fw.bin",
        size=10,
        modified_time=20,
        download_status="pending",
        upload_status="pending",
    )

    store.save({"images/fw.bin": record})
    loaded = store.load()

    assert loaded["images/fw.bin"] == record


def test_settings_store_returns_defaults_when_file_is_missing(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")

    settings = store.load()

    assert settings == AppSettings.default(tmp_path)


def test_default_settings_use_corrected_source_host_and_path() -> None:
    settings = AppSettings.default(Path("C:/tmp/tftp-monitor"))

    assert settings.source_host == "10.55.2.104"
    assert settings.source_files == []
    assert settings.source_password == ""
    assert settings.source_key_path == ""
    assert settings.destination_password == ""
    assert settings.destination_key_path == ""
    assert settings.reverse_source_files == []
    assert settings.reverse_destination_path == "/tftpboot"
    assert settings.monitor_direction == "forward"
    assert settings.local_cache_dir == Path("C:/tmp/tftp-monitor")


def test_default_settings_use_documents_dsmonitor_when_no_app_dir_is_supplied() -> None:
    settings = AppSettings.default()

    assert settings.app_data_dir == Path.home() / "Documents" / "dsmonitor"
    assert settings.local_cache_dir == Path.home() / "Documents" / "dsmonitor"


def test_settings_store_round_trips_multiple_source_files(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    settings = AppSettings.default(tmp_path)
    settings.source_files = ["/tftpboot/image.bin", "/tftpboot/boot.bin"]
    settings.source_host = "10.55.2.200"
    settings.source_password = "source-secret"
    settings.source_key_path = "C:/Users/LiWei/.ssh/source_id_rsa"
    settings.destination_host = "10.71.1.9"
    settings.destination_password = "target-secret"
    settings.destination_key_path = "C:/Users/LiWei/.ssh/target_id_rsa"
    settings.reverse_source_files = ["/home/tsl/image.bin"]
    settings.reverse_destination_path = "/tftpboot/reverse"
    settings.monitor_direction = "reverse"

    store.save(settings)
    loaded = store.load()

    assert loaded.source_files == ["/tftpboot/image.bin", "/tftpboot/boot.bin"]
    assert loaded.source_host == "10.55.2.200"
    assert loaded.source_password == "source-secret"
    assert loaded.source_key_path == "C:/Users/LiWei/.ssh/source_id_rsa"
    assert loaded.destination_host == "10.71.1.9"
    assert loaded.destination_password == "target-secret"
    assert loaded.destination_key_path == "C:/Users/LiWei/.ssh/target_id_rsa"
    assert loaded.reverse_source_files == ["/home/tsl/image.bin"]
    assert loaded.reverse_destination_path == "/tftpboot/reverse"
    assert loaded.monitor_direction == "reverse"


def test_settings_store_migrates_legacy_source_path_to_source_files(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.path.write_text(
        """
{
  "source_host": "10.55.2.104",
  "source_user": "wei.li",
  "source_path": "/tftpboot/image.bin",
  "destination_host": "10.71.1.3",
  "destination_user": "tsl",
  "destination_password": "",
  "destination_path": "/home/tsl",
  "poll_interval_seconds": 5,
  "local_cache_dir": "C:/tmp/cache",
  "app_data_dir": "C:/tmp/app"
}
""".strip(),
        encoding="utf-8",
    )

    loaded = store.load()

    assert loaded.source_files == ["/tftpboot/image.bin"]
    assert loaded.source_directories == []
    assert loaded.source_password == ""
    assert loaded.source_key_path == ""
    assert loaded.destination_key_path == ""
    assert loaded.reverse_source_files == []
    assert loaded.reverse_destination_path == "/tftpboot"
    assert loaded.monitor_direction == "forward"
