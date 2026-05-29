from pathlib import Path

from PySide6.QtWidgets import QApplication

from tftp_monitor.gui import MainWindow, format_source_directory_summary, format_status_summary
from tftp_monitor.manifest_store import ManifestStore
from tftp_monitor.models import SyncCycleResult
from tftp_monitor.settings_store import SettingsStore


def test_format_status_summary_includes_core_counts() -> None:
    result = SyncCycleResult(scanned_files=5, changed_files=2, synced_files=2, failed_files=1)
    assert format_status_summary(result) == "Scanned 5 | Changed 2 | Synced 2 | Failed 1"


def test_format_source_directory_summary_joins_multiple_roots() -> None:
    assert format_source_directory_summary(["/tftpboot", "/home/wei.li"]) == "/tftpboot; /home/wei.li"


def test_main_window_reads_editable_endpoint_fields(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    window.source_host_edit.setText("10.55.2.200")
    window.source_user_edit.setText("wei.li")
    window.source_directories_list.clear()
    window.source_directories_list.addItem("/tftpboot")
    window.source_directories_list.addItem("/home/wei.li")
    window.destination_host_edit.setText("10.71.1.9")
    window.destination_user_edit.setText("tsl")
    window.destination_password_edit.setText("secret")
    window.destination_path_edit.setText("/home/tsl")
    window.local_cache_edit.setText(str(tmp_path / "cache"))

    settings = window._current_settings_from_form()

    assert settings.source_host == "10.55.2.200"
    assert settings.source_directories == ["/tftpboot", "/home/wei.li"]
    assert settings.destination_host == "10.71.1.9"
    assert settings.destination_password == "secret"
    window.close()
