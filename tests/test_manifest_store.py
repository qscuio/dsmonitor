from pathlib import Path

from tftp_monitor.manifest_store import ManifestStore
from tftp_monitor.models import AppSettings, FileRecord
from tftp_monitor.settings_store import SettingsStore


def test_manifest_store_round_trips_records(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.json")
    record = FileRecord(
        relative_path="images/fw.bin",
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
    assert settings.source_path == "/home/wei.li"
