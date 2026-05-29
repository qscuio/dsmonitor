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
    assert settings.source_directories == ["/tftpboot", "/home/wei.li"]


def test_settings_store_round_trips_multiple_source_directories(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    settings = AppSettings.default(tmp_path)
    settings.source_directories = ["/tftpboot", "/home/wei.li", "/opt/images"]
    settings.source_host = "10.55.2.200"
    settings.destination_host = "10.71.1.9"

    store.save(settings)
    loaded = store.load()

    assert loaded.source_directories == ["/tftpboot", "/home/wei.li", "/opt/images"]
    assert loaded.source_host == "10.55.2.200"
    assert loaded.destination_host == "10.71.1.9"
