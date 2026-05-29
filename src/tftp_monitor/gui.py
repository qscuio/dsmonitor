from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .manifest_store import ManifestStore
from .models import AppSettings, SyncCycleResult, SyncEvent
from .settings_store import SettingsStore
from .ssh_sync import SshSyncService


def format_status_summary(result: SyncCycleResult) -> str:
    return (
        f"Scanned {result.scanned_files} | "
        f"Changed {result.changed_files} | "
        f"Synced {result.synced_files} | "
        f"Failed {result.failed_files}"
    )


def format_source_directory_summary(source_directories: list[str]) -> str:
    return "; ".join(source_directories)


class SyncController(QObject):
    event_received = Signal(object)
    cycle_finished = Signal(object)
    internal_cycle_completed = Signal(object)

    def __init__(
        self,
        service_factory: Callable[[AppSettings, Callable[[SyncEvent], None]], SshSyncService],
    ) -> None:
        super().__init__()
        self._service_factory = service_factory
        self._settings: AppSettings | None = None
        self._running = False
        self._cycle_active = False
        self._rescan_requested = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._queue_cycle)
        self.internal_cycle_completed.connect(self._handle_cycle_complete)

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, settings: AppSettings) -> None:
        self._settings = settings
        self._running = True
        self._timer.stop()
        self._queue_cycle()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    def trigger_rescan(self, settings: AppSettings) -> None:
        self._settings = settings
        self._timer.stop()
        if self._cycle_active:
            self._rescan_requested = True
            return
        self._queue_cycle()

    def _queue_cycle(self) -> None:
        if not self._settings or self._cycle_active:
            return
        self._cycle_active = True
        worker = threading.Thread(target=self._run_cycle, daemon=True)
        worker.start()

    def _run_cycle(self) -> None:
        assert self._settings is not None
        try:
            service = self._service_factory(self._settings, self.event_received.emit)
            result = service.sync_once()
        except Exception as exc:
            result = SyncCycleResult(
                failed_files=1,
                last_error=str(exc),
                activity="error",
            )
        self.internal_cycle_completed.emit(result)

    @Slot(object)
    def _handle_cycle_complete(self, result: SyncCycleResult) -> None:
        self._cycle_active = False
        self.cycle_finished.emit(result)
        if self._rescan_requested:
            self._rescan_requested = False
            self._queue_cycle()
            return
        if self._running and self._settings is not None:
            self._timer.start(self._settings.poll_interval_seconds * 1000)


class MainWindow(QMainWindow):
    def __init__(self, settings_store: SettingsStore, manifest_store: ManifestStore) -> None:
        super().__init__()
        self.settings_store = settings_store
        self.manifest_store = manifest_store
        self.settings = self.settings_store.load()
        self.controller = SyncController(self._build_service)
        self.controller.event_received.connect(self._handle_event)
        self.controller.cycle_finished.connect(self._handle_cycle_finished)
        self._build_ui()
        self._load_settings_into_form()

    def _build_ui(self) -> None:
        self.setWindowTitle("TFTP Monitor")
        self.resize(900, 640)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)

        status_group = QGroupBox("Status")
        status_layout = QGridLayout(status_group)
        self.source_status_value = QLabel("Idle")
        self.destination_status_value = QLabel("Idle")
        self.activity_value = QLabel("Idle")
        self.current_file_value = QLabel("-")
        self.summary_value = QLabel("Scanned 0 | Changed 0 | Synced 0 | Failed 0")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        status_layout.addWidget(QLabel("Source"), 0, 0)
        status_layout.addWidget(self.source_status_value, 0, 1)
        status_layout.addWidget(QLabel("Destination"), 1, 0)
        status_layout.addWidget(self.destination_status_value, 1, 1)
        status_layout.addWidget(QLabel("Activity"), 2, 0)
        status_layout.addWidget(self.activity_value, 2, 1)
        status_layout.addWidget(QLabel("Current File"), 3, 0)
        status_layout.addWidget(self.current_file_value, 3, 1)
        status_layout.addWidget(QLabel("Summary"), 4, 0)
        status_layout.addWidget(self.summary_value, 4, 1)
        status_layout.addWidget(QLabel("Progress"), 5, 0)
        status_layout.addWidget(self.progress_bar, 5, 1)

        settings_group = QGroupBox("Settings")
        settings_layout = QFormLayout(settings_group)
        self.source_host_edit = QLineEdit()
        self.source_user_edit = QLineEdit()
        self.source_directories_list = QListWidget()
        self.add_source_dir_button = QPushButton("Add")
        self.edit_source_dir_button = QPushButton("Edit")
        self.remove_source_dir_button = QPushButton("Remove")
        source_dirs_controls = QHBoxLayout()
        source_dirs_controls.addWidget(self.add_source_dir_button)
        source_dirs_controls.addWidget(self.edit_source_dir_button)
        source_dirs_controls.addWidget(self.remove_source_dir_button)
        source_dirs_container = QWidget()
        source_dirs_layout = QVBoxLayout(source_dirs_container)
        source_dirs_layout.setContentsMargins(0, 0, 0, 0)
        source_dirs_layout.addWidget(self.source_directories_list)
        source_dirs_layout.addLayout(source_dirs_controls)
        self.destination_host_edit = QLineEdit()
        self.destination_user_edit = QLineEdit()
        self.destination_password_edit = QLineEdit()
        self.destination_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.destination_path_edit = QLineEdit()
        self.poll_interval_spinbox = QSpinBox()
        self.poll_interval_spinbox.setRange(1, 3600)
        self.local_cache_edit = QLineEdit()
        settings_layout.addRow("Source Host", self.source_host_edit)
        settings_layout.addRow("Source User", self.source_user_edit)
        settings_layout.addRow("Source Directories", source_dirs_container)
        settings_layout.addRow("Destination Host", self.destination_host_edit)
        settings_layout.addRow("Destination User", self.destination_user_edit)
        settings_layout.addRow("Destination Password", self.destination_password_edit)
        settings_layout.addRow("Destination Root", self.destination_path_edit)
        settings_layout.addRow("Poll Interval (s)", self.poll_interval_spinbox)
        settings_layout.addRow("Local Cache", self.local_cache_edit)

        controls_layout = QHBoxLayout()
        self.start_button = QPushButton("Start Monitoring")
        self.stop_button = QPushButton("Stop Monitoring")
        self.rescan_button = QPushButton("Rescan Now")
        self.open_folder_button = QPushButton("Open Local Folder")
        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.stop_button)
        controls_layout.addWidget(self.rescan_button)
        controls_layout.addWidget(self.open_folder_button)

        events_group = QGroupBox("Recent Events")
        events_layout = QVBoxLayout(events_group)
        self.events_list = QListWidget()
        events_layout.addWidget(self.events_list)

        root_layout.addWidget(status_group)
        root_layout.addWidget(settings_group)
        root_layout.addLayout(controls_layout)
        root_layout.addWidget(events_group)

        self.start_button.clicked.connect(self._start_monitoring)
        self.stop_button.clicked.connect(self._stop_monitoring)
        self.rescan_button.clicked.connect(self._rescan_now)
        self.open_folder_button.clicked.connect(self._open_local_folder)
        self.add_source_dir_button.clicked.connect(self._add_source_directory)
        self.edit_source_dir_button.clicked.connect(self._edit_source_directory)
        self.remove_source_dir_button.clicked.connect(self._remove_source_directory)
        self._set_running_state(False)

    def _build_service(
        self,
        settings: AppSettings,
        event_callback: Callable[[SyncEvent], None],
    ) -> SshSyncService:
        return SshSyncService(
            settings=settings,
            manifest_store=self.manifest_store,
            event_callback=event_callback,
        )

    def _load_settings_into_form(self) -> None:
        self.source_host_edit.setText(self.settings.source_host)
        self.source_user_edit.setText(self.settings.source_user)
        self.source_directories_list.clear()
        for source_directory in self.settings.source_directories:
            self.source_directories_list.addItem(source_directory)
        self.destination_host_edit.setText(self.settings.destination_host)
        self.destination_user_edit.setText(self.settings.destination_user)
        self.destination_password_edit.setText(self.settings.destination_password)
        self.destination_path_edit.setText(self.settings.destination_path)
        self.poll_interval_spinbox.setValue(self.settings.poll_interval_seconds)
        self.local_cache_edit.setText(str(self.settings.local_cache_dir))

    def _current_settings_from_form(self) -> AppSettings:
        local_cache_dir = Path(self.local_cache_edit.text().strip())
        return AppSettings(
            source_host=self.source_host_edit.text().strip(),
            source_user=self.source_user_edit.text().strip(),
            source_directories=[
                self.source_directories_list.item(index).text()
                for index in range(self.source_directories_list.count())
            ],
            destination_host=self.destination_host_edit.text().strip(),
            destination_user=self.destination_user_edit.text().strip(),
            destination_password=self.destination_password_edit.text(),
            destination_path=self.destination_path_edit.text().strip(),
            poll_interval_seconds=self.poll_interval_spinbox.value(),
            local_cache_dir=local_cache_dir,
            app_data_dir=self.settings.app_data_dir,
        )

    def _persist_form_settings(self) -> AppSettings | None:
        local_cache = self.local_cache_edit.text().strip()
        if not local_cache:
            QMessageBox.warning(self, "Missing Cache Path", "Local cache path cannot be empty.")
            return None
        if not self.source_host_edit.text().strip():
            QMessageBox.warning(self, "Missing Source Host", "Source host cannot be empty.")
            return None
        if not self.source_user_edit.text().strip():
            QMessageBox.warning(self, "Missing Source User", "Source user cannot be empty.")
            return None
        if self.source_directories_list.count() == 0:
            QMessageBox.warning(self, "Missing Source Directories", "Add at least one source directory.")
            return None
        if not self.destination_host_edit.text().strip():
            QMessageBox.warning(self, "Missing Destination Host", "Destination host cannot be empty.")
            return None
        if not self.destination_user_edit.text().strip():
            QMessageBox.warning(self, "Missing Destination User", "Destination user cannot be empty.")
            return None
        if not self.destination_path_edit.text().strip():
            QMessageBox.warning(self, "Missing Destination Root", "Destination root cannot be empty.")
            return None
        self.settings = self._current_settings_from_form()
        self.settings_store.save(self.settings)
        return self.settings

    def _set_running_state(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    @Slot()
    def _start_monitoring(self) -> None:
        settings = self._persist_form_settings()
        if settings is None:
            return
        self.controller.start(settings)
        self._set_running_state(True)
        self._append_event("Monitoring started")

    @Slot()
    def _stop_monitoring(self) -> None:
        self.controller.stop()
        self._set_running_state(False)
        self.activity_value.setText("Stopped")
        self._append_event("Monitoring stopped")

    @Slot()
    def _rescan_now(self) -> None:
        settings = self._persist_form_settings()
        if settings is None:
            return
        self.controller.trigger_rescan(settings)
        self._append_event("Manual rescan requested")

    @Slot()
    def _open_local_folder(self) -> None:
        settings = self._persist_form_settings()
        if settings is None:
            return
        settings.local_cache_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(settings.local_cache_dir)))

    @Slot(object)
    def _handle_event(self, event: SyncEvent) -> None:
        self.activity_value.setText(event.activity.capitalize())
        self.current_file_value.setText(event.current_file or "-")
        self._append_event(event.message)
        if event.kind == "scan_started":
            self.source_status_value.setText("Scanning")
            self.destination_status_value.setText("Waiting")
            self.progress_bar.setRange(0, 0)
        elif event.kind == "download_progress":
            self.source_status_value.setText("Connected")
            self.destination_status_value.setText("Waiting")
            self._set_progress(event.bytes_transferred, event.total_bytes)
        elif event.kind == "upload_progress":
            self.source_status_value.setText("Connected")
            self.destination_status_value.setText("Connected")
            self._set_progress(event.bytes_transferred, event.total_bytes)
        elif event.kind == "file_synced":
            self.source_status_value.setText("Connected")
            self.destination_status_value.setText("Connected")
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
        elif event.kind == "file_failed":
            self.source_status_value.setText("Error")
            self.destination_status_value.setText("Error")

    @Slot(object)
    def _handle_cycle_finished(self, result: SyncCycleResult) -> None:
        self.summary_value.setText(format_status_summary(result))
        if result.activity == "idle":
            self.activity_value.setText("Idle")
            self.progress_bar.setRange(0, 100)
            if result.changed_files == 0:
                self.progress_bar.setValue(0)
        else:
            self.activity_value.setText("Error")
        if result.last_error:
            self._append_event(f"Error: {result.last_error}")
        if not self.controller.is_running:
            self._set_running_state(False)

    def _append_event(self, message: str) -> None:
        self.events_list.insertItem(0, message)
        while self.events_list.count() > 200:
            self.events_list.takeItem(self.events_list.count() - 1)

    def _set_progress(self, transferred: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setRange(0, 0)
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(transferred)

    @Slot()
    def _add_source_directory(self) -> None:
        source_directory, accepted = QInputDialog.getText(
            self,
            "Add Source Directory",
            "Source directory",
        )
        if accepted and source_directory.strip():
            self.source_directories_list.addItem(source_directory.strip())

    @Slot()
    def _edit_source_directory(self) -> None:
        current_item = self.source_directories_list.currentItem()
        if current_item is None:
            return
        updated_directory, accepted = QInputDialog.getText(
            self,
            "Edit Source Directory",
            "Source directory",
            text=current_item.text(),
        )
        if accepted and updated_directory.strip():
            current_item.setText(updated_directory.strip())

    @Slot()
    def _remove_source_directory(self) -> None:
        current_row = self.source_directories_list.currentRow()
        if current_row >= 0:
            self.source_directories_list.takeItem(current_row)
