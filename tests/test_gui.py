import threading
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QSystemTrayIcon

from tftp_monitor.gui import (
    MainWindow,
    REMOTE_SOURCE_MIME,
    RemoteFileList,
    build_remote_path_completions,
    create_dsmonitor_tray_icon,
    format_file_size,
    format_file_state,
    format_source_file_summary,
    format_status_summary,
    format_timestamp,
    format_transfer_rate,
    initial_remote_directory,
    parse_monitor_paths,
    remote_completion_context,
)
from tftp_monitor.manifest_store import ManifestStore
from tftp_monitor.models import SyncCycleResult, SyncEvent
from tftp_monitor.proxy_tunnel import load_proxy_config
from tftp_monitor.settings_store import SettingsStore
from tftp_monitor.ssh_sync import RemoteDirectoryEntry


def close_window(window: MainWindow) -> None:
    window._quit_requested = True
    window.tray_icon.hide()
    window.close()
    window.deleteLater()
    QApplication.processEvents()


def wait_until(predicate, timeout_ms: int = 1000) -> None:
    elapsed = 0
    while elapsed < timeout_ms:
        QApplication.processEvents()
        if predicate():
            return
        QTest.qWait(20)
        elapsed += 20
    assert predicate()


class FakeDropEvent:
    def __init__(self, mime_data: QMimeData, point: QPoint) -> None:
        self._mime_data = mime_data
        self._point = QPointF(point)

    def mimeData(self) -> QMimeData:
        return self._mime_data

    def position(self) -> QPointF:
        return self._point


def local_file_mime(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def test_format_status_summary_includes_core_counts() -> None:
    result = SyncCycleResult(scanned_files=5, changed_files=2, synced_files=2, failed_files=1)
    assert format_status_summary(result) == "Scanned 5 | Changed 2 | Synced 2 | Failed 1"


def test_format_source_file_summary_joins_multiple_files() -> None:
    assert format_source_file_summary(["/tftpboot/image.bin", "/tftpboot/boot.bin"]) == (
        "/tftpboot/image.bin; /tftpboot/boot.bin"
    )


def test_format_timestamp_uses_local_readable_time() -> None:
    assert format_timestamp(0) == "-"
    assert format_timestamp(100) != "-"


def test_format_file_size_uses_readable_units() -> None:
    assert format_file_size(0) == "-"
    assert format_file_size(512) == "512 B"
    assert format_file_size(2048) == "2.0 KB"


def test_format_transfer_rate_uses_readable_units() -> None:
    assert format_transfer_rate(0) == "-"
    assert format_transfer_rate(512) == "512 B/s"
    assert format_transfer_rate(1024 * 1024) == "1.0 MB/s"


def test_format_file_state_combines_path_timestamp_and_size() -> None:
    assert format_file_state("", 0, 0) == "-"
    state = format_file_state("/tftpboot/image.bin", 100, 2048)
    assert state == f"/tftpboot/image.bin | {format_timestamp(100)} | 2.0 KB"


def test_remote_completion_context_uses_parent_directory_and_typed_prefix() -> None:
    assert remote_completion_context("/tftpboot/V88") == ("/tftpboot", "V88")
    assert remote_completion_context("/tftpboot/") == ("/tftpboot", "")
    assert remote_completion_context("V88") == ("/tftpboot", "V88")


def test_build_remote_path_completions_filters_entries_by_prefix() -> None:
    entries = [
        RemoteDirectoryEntry("V8888_dev.x", "/tftpboot/V8888_dev.x", False, 10, 20),
        RemoteDirectoryEntry("V8500_DEBUG", "/tftpboot/V8500_DEBUG", True, 0, 30),
        RemoteDirectoryEntry("other.bin", "/tftpboot/other.bin", False, 10, 20),
    ]

    assert build_remote_path_completions(entries, "V8") == [
        "/tftpboot/V8500_DEBUG/",
        "/tftpboot/V8888_dev.x",
    ]


def test_dsmonitor_tray_icon_is_custom_and_named() -> None:
    app = QApplication.instance() or QApplication([])
    icon = create_dsmonitor_tray_icon()

    assert not icon.isNull()
    assert icon.availableSizes()


def test_main_window_reads_editable_endpoint_fields(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    window.source_host_edit.setText("10.55.2.200")
    window.source_user_edit.setText("wei.li")
    window.source_password_edit.setText("source-secret")
    window.source_key_path_edit.setText("C:/Users/LiWei/.ssh/source_id_rsa")
    window.source_file_edit.setText("/tftpboot/image.bin")
    window.destination_host_edit.setText("10.71.1.9")
    window.destination_user_edit.setText("tsl")
    window.destination_password_edit.setText("secret")
    window.destination_key_path_edit.setText("C:/Users/LiWei/.ssh/destination_id_rsa")
    window.destination_path_edit.setText("/home/tsl")
    window.local_cache_edit.setText(str(tmp_path / "cache"))

    settings = window._current_settings_from_form()

    assert settings.source_host == "10.55.2.200"
    assert settings.source_password == "source-secret"
    assert settings.source_key_path == "C:/Users/LiWei/.ssh/source_id_rsa"
    assert settings.source_files == ["/tftpboot/image.bin"]
    assert settings.source_directories == []
    assert settings.destination_host == "10.71.1.9"
    assert settings.destination_password == "secret"
    assert settings.destination_key_path == "C:/Users/LiWei/.ssh/destination_id_rsa"
    close_window(window)


def test_monitor_path_input_supports_multiple_comma_separated_files(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window.source_file_edit.setText("/tftpboot/V8888_dev.x,/tftpboot/V8500_SFU.6.10.x")
    window.reverse_source_file_edit.setText("/home/tsl/a.x; /home/tsl/b.x")

    settings = window._current_settings_from_form()
    reverse_settings = window._reverse_monitor_settings_from_form()

    assert parse_monitor_paths(" /a, /b; /c\n/d ") == ["/a", "/b", "/c", "/d"]
    assert settings.source_files == ["/tftpboot/V8888_dev.x", "/tftpboot/V8500_SFU.6.10.x"]
    assert settings.reverse_source_files == ["/home/tsl/a.x", "/home/tsl/b.x"]
    assert reverse_settings.source_files == ["/home/tsl/a.x", "/home/tsl/b.x"]

    close_window(window)


def test_monitor_path_input_preserves_wildcard_paths(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window.source_file_edit.setText("/tftpboot/*.x, /tftpboot/V8500*")
    window.reverse_source_file_edit.setText("/home/tsl/*.x; /home/tsl/V8500*")

    settings = window._current_settings_from_form()
    reverse_settings = window._reverse_monitor_settings_from_form()

    assert settings.source_files == ["/tftpboot/*.x", "/tftpboot/V8500*"]
    assert reverse_settings.source_files == ["/home/tsl/*.x", "/home/tsl/V8500*"]

    close_window(window)


def test_main_window_exposes_remote_source_file_browser(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    assert window.browse_source_file_button.text() == "Browse..."
    assert window.source_file_edit.completer() is not None
    assert not window.source_file_edit.isReadOnly()
    assert window.monitor_source_directory_edit.currentText() == "/tftpboot"
    assert window.monitor_source_directory_edit.completer() is not None
    assert window.monitor_source_list.topLevelItemCount() == 0

    close_window(window)


def test_source_file_completion_lists_remote_candidates(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    def fake_list_remote_directory(host, username, password, key_path, directory):
        assert host == "10.55.2.104"
        assert directory == "/tftpboot"
        return [
            RemoteDirectoryEntry("V8888_dev.x", "/tftpboot/V8888_dev.x", False, 10, 20),
            RemoteDirectoryEntry("other.bin", "/tftpboot/other.bin", False, 10, 20),
        ]

    monkeypatch.setattr("tftp_monitor.gui.list_remote_directory", fake_list_remote_directory)

    window.source_file_edit.setText("/tftpboot/V8")
    window._refresh_source_file_completions()

    assert window.source_file_completion_model.stringList() == ["/tftpboot/V8888_dev.x"]

    close_window(window)


def test_source_file_completion_preserves_existing_monitor_paths(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    def fake_list_remote_directory(host, username, password, key_path, directory):
        assert directory == "/tftpboot"
        return [RemoteDirectoryEntry("V8500_SFU.6.10.x", "/tftpboot/V8500_SFU.6.10.x", False, 10, 20)]

    monkeypatch.setattr("tftp_monitor.gui.list_remote_directory", fake_list_remote_directory)

    window.source_file_edit.setText("/tftpboot/V8888_dev.x, /tftpboot/V85")
    window._refresh_source_file_completions()

    assert window.source_file_completion_model.stringList() == [
        "/tftpboot/V8888_dev.x, /tftpboot/V8500_SFU.6.10.x"
    ]

    close_window(window)


def test_monitor_embedded_source_browser_selects_file(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    def fake_list_remote_directory(host, username, password, key_path, directory):
        assert host == "10.55.2.104"
        assert directory == "/tftpboot"
        return [RemoteDirectoryEntry("V8888_dev.x", "/tftpboot/V8888_dev.x", False, 10, 20)]

    monkeypatch.setattr("tftp_monitor.gui.list_remote_directory", fake_list_remote_directory)

    window._refresh_monitor_source_list()
    window._open_monitor_source_item(window.monitor_source_list.topLevelItem(0))

    assert window.source_file_edit.text() == "/tftpboot/V8888_dev.x"
    assert "Monitoring source selected" in window.status_message_value.text()

    close_window(window)


def test_monitor_embedded_source_browser_appends_file(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    def fake_list_remote_directory(host, username, password, key_path, directory):
        return [
            RemoteDirectoryEntry("V8500_SFU.6.10.x", "/tftpboot/V8500_SFU.6.10.x", False, 10, 20),
        ]

    monkeypatch.setattr("tftp_monitor.gui.list_remote_directory", fake_list_remote_directory)

    window.source_file_edit.setText("/tftpboot/V8888_dev.x")
    window._refresh_monitor_source_list()
    window._open_monitor_source_item(window.monitor_source_list.topLevelItem(0))

    assert window.source_file_edit.text() == "/tftpboot/V8888_dev.x, /tftpboot/V8500_SFU.6.10.x"

    close_window(window)


def test_main_window_has_transfer_tab_with_drag_drop(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    assert window.tabs.tabText(0) == "Monitor"
    assert window.tabs.tabText(1) == "Reverse Monitor"
    assert window.tabs.tabText(2) == "Transfer"
    assert not window.windowIcon().isNull()
    assert window.transfer_local_drop_zone.acceptDrops()
    assert window.transfer_source_list.acceptDrops()
    assert window.transfer_target_list.acceptDrops()
    assert window.transfer_source_directory_edit.currentText() == "/tftpboot"
    assert window.transfer_target_directory_edit.currentText() == "/home/tsl"
    assert window.transfer_source_directory_edit.completer() is not None
    assert window.transfer_target_directory_edit.completer() is not None
    assert window.transfer_source_directory_edit.findText("/home/wei.li") >= 0
    assert window.transfer_target_directory_edit.findText("/home") >= 0
    assert not hasattr(window, "transfer_upload_button")
    assert not hasattr(window, "transfer_source_file_edit")

    close_window(window)


def test_monitor_tab_uses_embedded_file_explorer_space(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    root_layout = window.centralWidget().layout()
    monitor_layout = window.tabs.widget(0).layout()

    assert window.tabs.currentIndex() == 0
    assert root_layout.stretch(root_layout.indexOf(window.tabs)) == 1
    assert monitor_layout.stretch(0) == 1

    close_window(window)


def test_transfer_tab_expands_file_lists_when_window_grows(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    root_layout = window.centralWidget().layout()
    transfer_layout = window.tabs.widget(2).layout()

    window.tabs.setCurrentIndex(2)

    assert root_layout.stretch(root_layout.indexOf(window.tabs)) == 1
    assert transfer_layout.stretch(transfer_layout.indexOf(window.transfer_browsers_row)) == 1

    close_window(window)


def test_transfer_tab_hides_monitor_status_to_keep_file_lists_usable(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    window.resize(1040, 620)
    window.show()
    app.processEvents()

    assert window.status_panel.isVisible()

    window.tabs.setCurrentIndex(2)
    app.processEvents()

    assert not window.status_panel.isVisible()
    assert window.transfer_browsers_row.height() >= 250

    window.tabs.setCurrentIndex(0)
    app.processEvents()
    assert window.status_panel.isVisible()

    close_window(window)


def test_remote_file_drag_includes_local_file_url_for_dragging_outside_window(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    local_file = tmp_path / "image.bin"
    local_file.write_bytes(b"payload")
    remote_list = RemoteFileList(drag_kind="source_remote")
    calls: list[tuple[str, str]] = []

    def provide_local_file(kind: str, remote_path: str) -> Path:
        calls.append((kind, remote_path))
        return local_file

    remote_list.external_file_provider = provide_local_file
    entry = RemoteDirectoryEntry("image.bin", "/tftpboot/image.bin", False, 7, 20)

    mime = remote_list._build_drag_mime(entry)

    assert calls == [("source_remote", "/tftpboot/image.bin")]
    assert mime.hasFormat(REMOTE_SOURCE_MIME)
    assert mime.hasUrls()
    assert Path(mime.urls()[0].toLocalFile()) == local_file
    assert bytes(
        mime.data('application/x-qt-windows-mime;value="Preferred DropEffect"')
    ) == (2).to_bytes(4, byteorder="little")


def test_transfer_tab_lists_both_remote_servers(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    def fake_list_remote_directory(host, username, password, key_path, directory):
        if host == "10.55.2.104":
            return [RemoteDirectoryEntry("V8888_dev.x", "/tftpboot/V8888_dev.x", False, 10, 20)]
        if host == "10.71.1.3":
            return [RemoteDirectoryEntry("target.bin", "/home/tsl/target.bin", False, 11, 21)]
        raise AssertionError(host)

    monkeypatch.setattr("tftp_monitor.gui.list_remote_directory", fake_list_remote_directory)

    window._refresh_transfer_source_list()
    window._refresh_transfer_target_list()

    wait_until(lambda: window.transfer_source_list.topLevelItemCount() == 1)
    wait_until(lambda: window.transfer_target_list.topLevelItemCount() == 1)
    assert window.transfer_source_list.topLevelItemCount() == 1
    assert window.transfer_source_list.topLevelItem(0).text(0) == "V8888_dev.x"
    assert "1970-01-01" in window.transfer_source_list.topLevelItem(0).text(1)
    assert window.transfer_source_list.topLevelItem(0).text(2) == "10 B"
    assert window.transfer_target_list.topLevelItemCount() == 1
    assert window.transfer_target_list.topLevelItem(0).text(0) == "target.bin"

    close_window(window)


def test_transfer_file_list_keeps_directories_first_and_remembers_paths(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    def fake_list_remote_directory(host, username, password, key_path, directory):
        assert directory == "/tftpboot/images"
        return [
            RemoteDirectoryEntry("z.bin", "/tftpboot/images/z.bin", False, 10, 20),
            RemoteDirectoryEntry("aaa", "/tftpboot/images/aaa", True, 0, 30),
        ]

    monkeypatch.setattr("tftp_monitor.gui.list_remote_directory", fake_list_remote_directory)

    window.transfer_source_directory_edit.setCurrentText("/tftpboot/images/")
    window._refresh_transfer_source_list()

    wait_until(lambda: window.transfer_source_list.topLevelItemCount() == 2)
    assert window.transfer_source_directory_edit.currentText() == "/tftpboot/images"
    assert window.transfer_source_directory_edit.findText("/tftpboot/images") >= 0
    assert window.transfer_source_directory_edit.findText("/tftpboot/images/aaa") >= 0
    assert window.transfer_source_list.topLevelItem(0).text(0) == "aaa"
    assert window.transfer_source_list.topLevelItem(1).text(0) == "z.bin"

    close_window(window)


def test_transfer_tab_refreshes_remote_lists_when_opened(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    calls: list[tuple[str, str]] = []

    def fake_list_remote_directory(host, username, password, key_path, directory):
        calls.append((host, directory))
        return []

    monkeypatch.setattr("tftp_monitor.gui.list_remote_directory", fake_list_remote_directory)

    window.tabs.setCurrentIndex(2)

    wait_until(lambda: len(calls) == 2)
    assert set(calls) == {("10.55.2.104", "/tftpboot"), ("10.71.1.3", "/home/tsl")}

    close_window(window)


def test_transfer_tab_refresh_does_not_block_gui(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    started = threading.Event()
    release = threading.Event()

    def fake_list_remote_directory(host, username, password, key_path, directory):
        started.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr("tftp_monitor.gui.list_remote_directory", fake_list_remote_directory)

    window.tabs.setCurrentIndex(2)

    assert started.wait(timeout=1)
    assert window.tabs.currentIndex() == 2
    assert "Listing" in window.transfer_status_value.text()

    release.set()
    wait_until(lambda: "Listed" in window.transfer_status_value.text(), timeout_ms=2000)

    close_window(window)


def test_window_drop_routes_local_files_to_hovered_remote_panel(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    local_file = tmp_path / "image.bin"
    local_file.write_bytes(b"payload")

    monkeypatch.setattr("tftp_monitor.gui.list_remote_directory", lambda *args: [])
    window.show()
    window.tabs.setCurrentIndex(2)
    app.processEvents()

    mime = local_file_mime(local_file)
    source_point = window.transfer_source_panel.mapTo(window, QPoint(12, 12))
    target_point = window.transfer_target_panel.mapTo(window, QPoint(12, 12))

    assert window._window_drop_target(FakeDropEvent(mime, source_point))[1] == "source"
    assert window._window_drop_target(FakeDropEvent(mime, target_point))[1] == "target"

    close_window(window)


def test_local_file_drop_auto_uploads_to_target(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    local_file = tmp_path / "image.bin"
    local_file.write_bytes(b"payload")
    uploads: list[tuple[Path, str]] = []

    def fake_upload(settings, local_path, remote_directory, progress):
        uploads.append((local_path, remote_directory))
        progress(7, 7)
        return type("Result", (), {"remote_path": f"{remote_directory}/{local_path.name}"})()

    monkeypatch.setattr("tftp_monitor.gui.upload_manual_file", fake_upload)

    window._handle_target_drop("local_files", [local_file])

    wait_until(lambda: "Uploaded image.bin" in window.transfer_status_value.text())
    assert uploads == [(local_file, "/home/tsl")]
    assert "Uploaded image.bin" in window.transfer_status_value.text()

    close_window(window)


def test_local_file_drop_auto_uploads_to_source(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    local_file = tmp_path / "image.bin"
    local_file.write_bytes(b"payload")
    uploads: list[tuple[Path, str]] = []

    def fake_upload(settings, local_path, remote_directory, progress):
        uploads.append((local_path, remote_directory))
        progress(7, 7)
        return type("Result", (), {"remote_path": f"{remote_directory}/{local_path.name}"})()

    monkeypatch.setattr("tftp_monitor.gui.upload_source_manual_file", fake_upload)

    window._handle_source_drop("local_files", [local_file])

    wait_until(lambda: "Uploaded image.bin" in window.transfer_status_value.text())
    assert uploads == [(local_file, "/tftpboot")]
    assert "10.55.2.104:/tftpboot" in window.transfer_status_value.text()

    close_window(window)


def test_transfer_runs_in_background_and_disables_drop_targets(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    local_file = tmp_path / "image.bin"
    local_file.write_bytes(b"payload")
    started = threading.Event()
    release = threading.Event()

    def fake_upload(settings, local_path, remote_directory, progress):
        started.set()
        release.wait(timeout=2)
        progress(7, 7)
        return type("Result", (), {"remote_path": f"{remote_directory}/{local_path.name}"})()

    monkeypatch.setattr("tftp_monitor.gui.upload_manual_file", fake_upload)

    window._handle_target_drop("local_files", [local_file])

    assert started.wait(timeout=1)
    assert not window.transfer_target_list.isEnabled()
    assert window.transfer_status_value.text() == "Uploading 1 file(s)..."

    release.set()
    wait_until(lambda: window.transfer_target_list.isEnabled())
    assert "Uploaded image.bin" in window.transfer_status_value.text()

    close_window(window)


def test_source_file_drop_downloads_to_local(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    downloads: list[tuple[str, Path]] = []

    def fake_download(settings, remote_path, local_directory, progress):
        downloads.append((remote_path, local_directory))
        progress(8, 8)
        local_path = local_directory / Path(remote_path).name
        return type("Result", (), {"local_path": local_path, "remote_path": remote_path})()

    monkeypatch.setattr("tftp_monitor.gui.download_manual_file", fake_download)

    window._handle_local_drop("source_remote", "/tftpboot/image.bin")

    wait_until(lambda: "Downloaded image.bin" in window.transfer_status_value.text())
    assert downloads == [("/tftpboot/image.bin", tmp_path)]
    assert "Downloaded image.bin" in window.transfer_status_value.text()

    close_window(window)


def test_target_file_drop_downloads_to_local(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    downloads: list[tuple[str, Path]] = []

    def fake_download(settings, remote_path, local_directory, progress):
        downloads.append((remote_path, local_directory))
        progress(8, 8)
        local_path = local_directory / Path(remote_path).name
        return type("Result", (), {"local_path": local_path, "remote_path": remote_path})()

    monkeypatch.setattr("tftp_monitor.gui.download_target_manual_file", fake_download)

    window._handle_local_drop("target_remote", "/home/tsl/image.bin")

    wait_until(lambda: "Downloaded image.bin" in window.transfer_status_value.text())
    assert downloads == [("/home/tsl/image.bin", tmp_path)]
    assert "Downloaded image.bin" in window.transfer_status_value.text()

    close_window(window)


def test_source_file_drop_copies_across_servers(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    copies: list[tuple[str, str, Path]] = []

    def fake_copy(settings, source_remote_path, destination_directory, temp_directory, progress):
        copies.append((source_remote_path, destination_directory, temp_directory))
        progress(9, 9)
        return type("Result", (), {"remote_path": f"{destination_directory}/image.bin"})()

    monkeypatch.setattr("tftp_monitor.gui.copy_source_to_destination", fake_copy)

    window._handle_target_drop("source_remote", "/tftpboot/image.bin")

    wait_until(lambda: "Copied image.bin" in window.transfer_status_value.text())
    assert copies == [("/tftpboot/image.bin", "/home/tsl", tmp_path / "transfer")]
    assert "Copied image.bin" in window.transfer_status_value.text()

    close_window(window)


def test_target_file_drop_copies_to_source_server(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    copies: list[tuple[str, str, Path]] = []

    def fake_copy(settings, target_remote_path, source_directory, temp_directory, progress):
        copies.append((target_remote_path, source_directory, temp_directory))
        progress(9, 9)
        return type("Result", (), {"remote_path": f"{source_directory}/image.bin"})()

    monkeypatch.setattr("tftp_monitor.gui.copy_destination_to_source", fake_copy)

    window._handle_source_drop("target_remote", "/home/tsl/image.bin")

    wait_until(lambda: "Copied image.bin" in window.transfer_status_value.text())
    assert copies == [("/home/tsl/image.bin", "/tftpboot", tmp_path / "transfer")]
    assert "10.55.2.104:/tftpboot/image.bin" in window.transfer_status_value.text()

    close_window(window)


def test_monitor_progress_bar_shows_transfer_rate(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window._handle_event(
        SyncEvent(
            kind="download_progress",
            message="Downloading image.bin",
            current_file="image.bin",
            bytes_transferred=1024,
            total_bytes=2048,
        )
    )

    assert "/s" in window.progress_bar.format()

    close_window(window)


def test_main_window_uses_compact_status_without_recent_events(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    assert not hasattr(window, "events_list")
    assert window.status_message_value.text() == "Ready"
    assert window.width() >= 1040
    assert window.height() >= 520
    assert window.windowTitle() == "dsmonitor"
    assert window.settings_button.text() == "Settings"
    assert window.tabs.tabText(3) == "Proxy"
    assert window.tabs.tabText(4) == "Settings"

    close_window(window)


def test_settings_button_opens_settings_tab(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    assert window.tabs.currentIndex() == 0

    window.settings_button.click()
    app.processEvents()
    assert window.tabs.currentWidget() is window.settings_fields_container

    close_window(window)


def test_reverse_monitor_tab_swaps_direction_and_uses_separate_manifest(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window.reverse_monitor_directory_edit.setCurrentText("/home/tsl/images")
    window._use_reverse_monitor_folder()
    window.reverse_destination_path_edit.setText("/tftpboot")

    settings = window._reverse_monitor_settings_from_form()
    service = window._build_service(settings, lambda _event: None)

    assert window.reverse_source_file_edit.text() == "/home/tsl/images"
    assert settings.monitor_direction == "reverse"
    assert settings.source_host == "10.71.1.3"
    assert settings.source_user == "tsl"
    assert settings.source_files == ["/home/tsl/images"]
    assert settings.destination_host == "10.55.2.104"
    assert settings.destination_user == "wei.li"
    assert settings.destination_path == "/tftpboot"
    assert settings.local_cache_dir == tmp_path / "reverse"
    assert service.manifest_store is window.reverse_manifest_store

    close_window(window)


def test_forward_and_reverse_monitor_controls_are_independent(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    assert window.forward_controller is not window.reverse_controller

    window._set_forward_running_state(True)
    assert not window.start_button.isEnabled()
    assert window.stop_button.isEnabled()
    assert window.reverse_start_button.isEnabled()
    assert not window.reverse_stop_button.isEnabled()

    window._set_reverse_running_state(True)
    assert not window.start_button.isEnabled()
    assert window.stop_button.isEnabled()
    assert not window.reverse_start_button.isEnabled()
    assert window.reverse_stop_button.isEnabled()

    window._set_forward_running_state(False)
    assert window.start_button.isEnabled()
    assert not window.stop_button.isEnabled()
    assert not window.reverse_start_button.isEnabled()
    assert window.reverse_stop_button.isEnabled()

    close_window(window)


def test_save_settings_button_persists_form_without_source_file(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings_path = tmp_path / "settings.json"
    window = MainWindow(
        settings_store=SettingsStore(settings_path),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window.source_file_edit.setText("")
    window.source_host_edit.setText("10.55.2.104")
    window.source_user_edit.setText("wei.li")
    window.source_password_edit.setText("source-secret")
    window.destination_host_edit.setText("10.71.1.3")
    window.destination_user_edit.setText("tsl")
    window.destination_password_edit.setText("target-secret")
    window.destination_path_edit.setText("/home/tsl")
    window.local_cache_edit.setText(str(tmp_path / "dsmonitor"))

    window.save_settings_button.click()
    app.processEvents()

    saved = SettingsStore(settings_path).load()
    assert saved.source_password == "source-secret"
    assert saved.destination_password == "target-secret"
    assert saved.source_files == []
    assert "Saved to" in window.settings_saved_label.text()

    close_window(window)


def test_proxy_tab_loads_default_session_and_saves_new_session(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    assert window.tabs.tabText(3) == "Proxy"
    assert window.proxy_tree.topLevelItemCount() == 3
    assert window.proxy_tree.topLevelItem(0).text(0) == "N2X"

    window._new_proxy()
    window.proxy_name_edit.setText("telnet-m4000")
    window.proxy_local_host_edit.setText("127.0.0.1")
    window.proxy_local_port_spin.setValue(10023)
    window.proxy_remote_host_edit.setText("10.71.20.134")
    window.proxy_remote_port_spin.setValue(23)
    window.proxy_jump_user_edit.setText("tsl")
    window.proxy_jump_host_edit.setText("10.71.1.3")

    window.proxy_save_button.click()
    app.processEvents()

    saved = load_proxy_config(tmp_path / "proxies.json")
    assert [tunnel.name for tunnel in saved.tunnels] == ["N2X", "M4000", "M4000-23", "telnet-m4000"]
    assert saved.tunnels[3].local_port == 10023
    assert window.proxy_tree.topLevelItemCount() == 4

    close_window(window)


def test_new_proxy_after_selecting_existing_appends_instead_of_overwriting(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window.proxy_tree.setCurrentItem(window.proxy_tree.topLevelItem(1))
    window.proxy_tree.topLevelItem(1).setSelected(True)
    app.processEvents()
    assert window.proxy_name_edit.text() == "M4000"

    window.proxy_new_button.click()
    app.processEvents()
    assert window.selected_proxy_index is None

    window.proxy_name_edit.setText("M4000-test")
    window.proxy_local_port_spin.setValue(10025)
    window.proxy_remote_host_edit.setText("10.71.20.230")
    window.proxy_remote_port_spin.setValue(10025)
    window.proxy_save_button.click()
    app.processEvents()

    saved = load_proxy_config(tmp_path / "proxies.json")
    assert [tunnel.name for tunnel in saved.tunnels] == ["N2X", "M4000", "M4000-23", "M4000-test"]
    assert saved.tunnels[1].local_port == 10004
    assert saved.tunnels[3].local_port == 10025

    close_window(window)


def test_proxy_session_name_column_does_not_stretch_across_table(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    assert window.proxy_tree.columnWidth(0) <= 140
    assert window.proxy_tree.columnWidth(2) >= window.proxy_tree.columnWidth(0)

    close_window(window)


def test_proxy_status_refresh_does_not_overwrite_name_being_edited(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window._new_proxy()
    window.proxy_name_edit.setText("V8500")
    window.proxy_local_port_spin.setValue(10003)
    window.proxy_remote_host_edit.setText("10.71.20.230")
    window.proxy_remote_port_spin.setValue(10003)
    window.proxy_save_button.click()
    app.processEvents()

    window.proxy_name_edit.setText("V8500-test")
    window._refresh_proxy_rows()
    app.processEvents()

    assert window.proxy_name_edit.text() == "V8500-test"

    close_window(window)


def test_proxy_tab_blocks_duplicate_name_and_local_endpoint(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    warnings: list[str] = []
    monkeypatch.setattr(
        "tftp_monitor.gui.QMessageBox.warning",
        lambda _parent, title, _message: warnings.append(title),
    )
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window._new_proxy()
    window.proxy_name_edit.setText("N2X")
    window.proxy_local_port_spin.setValue(10024)
    window.proxy_save_button.click()
    app.processEvents()

    window.proxy_name_edit.setText("unique-name")
    window.proxy_local_port_spin.setValue(13389)
    window.proxy_save_button.click()
    app.processEvents()

    assert warnings == ["Duplicate tunnel name", "Duplicate local port"]
    assert window.proxy_tree.topLevelItemCount() == 3

    close_window(window)


def test_progress_event_updates_single_status_message(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window._handle_event(
        SyncEvent(
            kind="upload_progress",
            message="Uploading V8888_dev.x",
            activity="uploading",
            current_file="V8888_dev.x",
            bytes_transferred=1,
            total_bytes=2,
        )
    )

    assert window.status_message_value.text() == "Uploading V8888_dev.x"
    assert not hasattr(window, "events_list")

    close_window(window)


def test_file_state_event_updates_file_paths_and_timestamps(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window._handle_event(
        SyncEvent(
            kind="file_state",
            message="File state refreshed",
            source_path="/tftpboot/V8888_dev.x",
            source_modified_time=100,
            source_size=1024,
            local_path=str(tmp_path / "cache" / "V8888_dev.x"),
            local_modified_time=200,
            local_size=2048,
            destination_path="/home/tsl/V8888_dev.x",
            destination_modified_time=300,
            destination_size=4096,
        )
    )

    assert window.source_file_state_value.text() == format_file_state("/tftpboot/V8888_dev.x", 100, 1024)
    assert window.local_file_state_value.text() == format_file_state(
        str(tmp_path / "cache" / "V8888_dev.x"),
        200,
        2048,
    )
    assert window.destination_file_state_value.text() == format_file_state("/home/tsl/V8888_dev.x", 300, 4096)

    close_window(window)


def test_primary_buttons_have_icons(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    assert not window.browse_source_file_button.icon().isNull()
    assert not window.monitor_source_up_button.icon().isNull()
    assert not window.monitor_source_refresh_button.icon().isNull()
    assert not window.start_button.icon().isNull()
    assert not window.stop_button.icon().isNull()
    assert not window.rescan_button.icon().isNull()
    assert not window.open_folder_button.icon().isNull()
    assert not window.settings_button.icon().isNull()

    close_window(window)


def test_initial_remote_directory_uses_source_file_parent() -> None:
    assert initial_remote_directory("/tftpboot/image.bin") == "/tftpboot"
    assert initial_remote_directory("") == "/tftpboot"


def test_main_window_minimizes_to_system_tray_and_restores(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    window.show()
    app.processEvents()

    assert window.tray_icon.isVisible()

    window.setWindowState(Qt.WindowState.WindowMinimized)
    app.processEvents()

    assert not window.isVisible()

    window._handle_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
    app.processEvents()

    assert window.isVisible()
    assert not window.isMinimized()

    close_window(window)


def test_main_window_close_button_hides_to_system_tray(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    messages: list[tuple[str, str]] = []
    window._show_tray_alert = lambda title, message: messages.append((title, message))
    window.show()
    app.processEvents()

    window.close()
    app.processEvents()

    assert not window.isVisible()
    assert window.tray_icon.isVisible()
    assert messages == [("dsmonitor", "Still running in the tray")]

    close_window(window)


def test_tray_alert_uses_compact_popup(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window._show_tray_alert("dsmonitor", "Synced V8888_dev.x")
    app.processEvents()

    assert window.compact_alert.isVisible()
    assert window.compact_alert.width() <= 320
    assert window.compact_alert.height() <= 90

    close_window(window)


def test_tray_alert_auto_hides_and_can_be_dismissed(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )

    window.compact_alert.show_alert("dsmonitor", "Synced V8888_dev.x", duration_ms=10)
    app.processEvents()
    assert window.compact_alert.isVisible()

    QTest.qWait(40)
    app.processEvents()
    assert not window.compact_alert.isVisible()

    window.compact_alert.show_alert("dsmonitor", "Synced V8888_dev.x", duration_ms=2000)
    app.processEvents()
    assert window.compact_alert.isVisible()

    QTest.mouseClick(window.compact_alert, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not window.compact_alert.isVisible()

    close_window(window)


def test_hidden_window_shows_tray_alert_when_file_is_updated(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        manifest_store=ManifestStore(tmp_path / "manifest.json"),
    )
    messages: list[tuple[str, str]] = []
    window._show_tray_alert = lambda title, message: messages.append((title, message))
    window.show()
    app.processEvents()
    window.setWindowState(Qt.WindowState.WindowMinimized)
    app.processEvents()

    window._handle_event(
        SyncEvent(
            kind="file_synced",
            message="Synced V8888_dev.x",
            current_file="V8888_dev.x",
        )
    )

    assert messages == [
        ("dsmonitor", "Still running in the tray"),
        ("dsmonitor", "Synced V8888_dev.x"),
    ]

    close_window(window)
