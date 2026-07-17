from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QPointF, QRectF, QEvent, QMimeData, QObject, QStringListModel, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QDesktopServices,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QCompleter,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QMenu,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .manifest_store import ManifestStore
from .models import AppSettings, SyncCycleResult, SyncEvent
from .proxy_credentials import delete_password, has_password, read_password, write_password
from .proxy_tunnel import (
    ProxyConfig,
    ProxyProcessManager,
    ProxyTunnel,
    build_proxy_startup_batch_content,
    current_exe_path,
    duplicate_local_endpoint,
    duplicate_tunnel_name,
    load_proxy_config,
    proxy_config_path,
    proxy_log_path,
    proxy_startup_batch_path,
    save_proxy_config,
)
from .settings_store import SettingsStore
from .ssh_sync import (
    RemoteDirectoryEntry,
    SshSyncService,
    copy_destination_to_source,
    copy_source_to_destination,
    download_manual_file,
    download_target_manual_file,
    list_remote_directory,
    upload_manual_file,
    upload_source_manual_file,
)

REMOTE_SOURCE_MIME = "application/x-dsmonitor-source-path"
REMOTE_TARGET_MIME = "application/x-dsmonitor-target-path"
DSMONITOR_ICON_RELATIVE_PATH = Path("assets") / "dsmonitor.ico"


def format_status_summary(result: SyncCycleResult) -> str:
    return (
        f"Scanned {result.scanned_files} | "
        f"Changed {result.changed_files} | "
        f"Synced {result.synced_files} | "
        f"Failed {result.failed_files}"
    )


def format_source_file_summary(source_files: list[str]) -> str:
    return "; ".join(source_files)


def parse_monitor_paths(text: str) -> list[str]:
    paths: list[str] = []
    for raw_path in text.replace(";", ",").replace("\r", "\n").replace("\n", ",").split(","):
        path = raw_path.strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def format_monitor_paths(paths: list[str]) -> str:
    return ", ".join(paths)


def monitor_completion_parts(text: str) -> tuple[str, str]:
    last_separator_index = max(text.rfind(","), text.rfind(";"), text.rfind("\n"))
    if last_separator_index < 0:
        return "", text.strip()
    prefix = text[: last_separator_index + 1]
    if prefix and not prefix.endswith((" ", "\n")):
        prefix = f"{prefix} "
    return prefix, text[last_separator_index + 1 :].strip()


def append_monitor_path(text: str, path: str) -> str:
    paths = parse_monitor_paths(text)
    if path not in paths:
        paths.append(path)
    return format_monitor_paths(paths)


def format_timestamp(timestamp: float) -> str:
    if timestamp <= 0:
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def format_file_size(size: int) -> str:
    if size <= 0:
        return "-"
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} B"
    return f"{value:.1f} {unit}"


def format_transfer_rate(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "-"
    return f"{format_file_size(int(bytes_per_second))}/s"


def format_file_state(path: str, timestamp: float, size: int) -> str:
    if not path:
        return "-"
    return f"{path} | {format_timestamp(timestamp)} | {format_file_size(size)}"


def initial_remote_directory(source_file: str) -> str:
    _prefix, active_path = monitor_completion_parts(source_file)
    cleaned = active_path.strip()
    if not cleaned or cleaned == "/":
        return "/tftpboot"
    path = PurePosixPath(cleaned)
    parent = str(path.parent)
    return parent if parent and parent != "." else "/tftpboot"


def normalize_remote_directory(directory: str, fallback: str) -> str:
    cleaned = directory.strip() or fallback
    if cleaned != "/":
        cleaned = cleaned.rstrip("/")
    return cleaned or "/"


def parent_remote_directory(directory: str) -> str:
    parent = str(PurePosixPath(normalize_remote_directory(directory, "/")).parent)
    return parent if parent and parent != "." else "/"


def remote_completion_context(text: str) -> tuple[str, str]:
    _prefix, active_path = monitor_completion_parts(text)
    cleaned = active_path.strip()
    if not cleaned:
        return "/tftpboot", ""
    if cleaned.endswith("/"):
        return cleaned.rstrip("/") or "/", ""
    if "/" not in cleaned.strip("/"):
        return "/tftpboot", cleaned.lstrip("/")
    path = PurePosixPath(cleaned)
    parent = str(path.parent)
    if not parent or parent == ".":
        parent = "/tftpboot"
    return parent, path.name


def build_remote_path_completions(entries: list[RemoteDirectoryEntry], prefix: str) -> list[str]:
    lowered_prefix = prefix.lower()
    matches: list[str] = []
    for entry in sorted(entries, key=lambda item: (not item.is_directory, item.name.lower())):
        if lowered_prefix and not entry.name.lower().startswith(lowered_prefix):
            continue
        suffix = "/" if entry.is_directory else ""
        matches.append(f"{entry.path}{suffix}")
    return matches


def parse_transfer_drop_payload(mime: QMimeData) -> tuple[str, object] | None:
    if mime.hasFormat(REMOTE_SOURCE_MIME):
        return "source_remote", bytes(mime.data(REMOTE_SOURCE_MIME)).decode("utf-8")
    if mime.hasFormat(REMOTE_TARGET_MIME):
        return "target_remote", bytes(mime.data(REMOTE_TARGET_MIME)).decode("utf-8")
    if mime.hasUrls():
        files = [
            Path(url.toLocalFile())
            for url in mime.urls()
            if url.isLocalFile() and Path(url.toLocalFile()).is_file()
        ]
        if files:
            return "local_files", files
    text = mime.text().strip()
    if text.startswith("dsmonitor-source:"):
        return "source_remote", text.removeprefix("dsmonitor-source:").strip()
    if text.startswith("dsmonitor-target:"):
        return "target_remote", text.removeprefix("dsmonitor-target:").strip()
    if text.startswith("/"):
        return "source_remote", text
    return None


def dsmonitor_icon_path() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return bundle_root / DSMONITOR_ICON_RELATIVE_PATH


def create_dsmonitor_icon() -> QIcon:
    icon_path = dsmonitor_icon_path()
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            return icon
    return create_dsmonitor_tray_icon()


def create_dsmonitor_tray_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    shadow = QRadialGradient(34, 38, 31)
    shadow.setColorAt(0, QColor(4, 18, 42, 115))
    shadow.setColorAt(1, QColor(4, 18, 42, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(shadow)
    painter.drawEllipse(QRectF(7, 8, 52, 52))

    background = QLinearGradient(12, 8, 52, 56)
    background.setColorAt(0, QColor("#1f64bc"))
    background.setColorAt(0.58, QColor("#237fd6"))
    background.setColorAt(1, QColor("#25bfd1"))
    painter.setBrush(background)
    painter.drawEllipse(QRectF(6, 5, 52, 52))

    shine = QLinearGradient(8, 4, 48, 30)
    shine.setColorAt(0, QColor(255, 255, 255, 118))
    shine.setColorAt(1, QColor(255, 255, 255, 0))
    painter.setBrush(shine)
    painter.drawEllipse(QRectF(13, 8, 32, 16))

    feather = QPainterPath()
    feather.moveTo(43, 8)
    feather.cubicTo(53, 17, 53, 29, 42, 41)
    feather.cubicTo(34, 50, 25, 56, 18, 54)
    feather.cubicTo(21, 46, 26, 35, 31, 24)
    feather.cubicTo(35, 16, 39, 10, 43, 8)

    feather_shadow = QPainterPath(feather)
    painter.save()
    painter.translate(2.2, 2.8)
    painter.setBrush(QColor(0, 26, 67, 75))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPath(feather_shadow)
    painter.restore()

    feather_fill = QLinearGradient(19, 9, 42, 55)
    feather_fill.setColorAt(0, QColor("#ffffff"))
    feather_fill.setColorAt(1, QColor("#9bddff"))
    painter.setBrush(feather_fill)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPath(feather)

    painter.setPen(QPen(QColor(255, 255, 255, 230), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(19, 54), QPointF(43, 8))
    painter.setPen(QPen(QColor(31, 106, 186, 160), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(19, 54), QPointF(43, 8))

    painter.setPen(QPen(QColor(31, 106, 186, 150), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for start, end in [
        (QPointF(30, 32), QPointF(18, 29)),
        (QPointF(34, 24), QPointF(23, 20)),
        (QPointF(37, 18), QPointF(28, 15)),
        (QPointF(29, 34), QPointF(42, 36)),
        (QPointF(33, 27), QPointF(45, 27)),
        (QPointF(37, 20), QPointF(46, 17)),
    ]:
        painter.drawLine(start, end)

    painter.setPen(QPen(QColor("#22c39a"), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(15, 44), QPointF(25, 44))
    painter.setBrush(QColor("#22c39a"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(QPolygonF([QPointF(25, 40), QPointF(33, 44), QPointF(25, 48)]))

    rim = QPainterPath()
    rim.addEllipse(QRectF(6, 5, 52, 52))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
    painter.drawPath(rim)
    painter.end()

    return QIcon(pixmap)


class CompactTrayAlert(QFrame):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(300, 72)
        self.setStyleSheet(
            """
            QFrame {
                background: #20242a;
                border: 1px solid #3d4653;
                border-radius: 6px;
            }
            QLabel {
                background: transparent;
                color: #ffffff;
                font-family: "Segoe UI", "Microsoft YaHei UI";
            }
            QLabel#AlertTitle {
                font-size: 9pt;
                font-weight: 700;
            }
            QLabel#AlertMessage {
                color: #d9dee7;
                font-size: 9pt;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(4)
        self.title_label = QLabel("dsmonitor")
        self.title_label.setObjectName("AlertTitle")
        self.message_label = QLabel("")
        self.message_label.setObjectName("AlertMessage")
        self.message_label.setWordWrap(False)
        layout.addWidget(self.title_label)
        layout.addWidget(self.message_label)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_alert(self, title: str, message: str, duration_ms: int = 2000) -> None:
        self.title_label.setText(title)
        self.message_label.setText(message)
        self._move_to_bottom_right()
        self.show()
        self.raise_()
        self.hide_timer.start(duration_ms)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.hide_timer.stop()
        self.hide()
        super().mousePressEvent(event)

    def _move_to_bottom_right(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.right() - self.width() - 16, available.bottom() - self.height() - 16)


class TransferDropZone(QFrame):
    dropped = Signal(str, object)

    def __init__(self, title: str, detail: str, action: str) -> None:
        super().__init__()
        self.action = action
        self.setAcceptDrops(True)
        self.setMinimumHeight(108)
        self.setStyleSheet(
            """
            QFrame {
                background: #ffffff;
                border: 1px dashed #9aa7b7;
                border-radius: 6px;
            }
            QLabel {
                background: transparent;
                color: #344054;
            }
            QLabel#DropTitle {
                font-size: 11pt;
                font-weight: 700;
            }
            QLabel#DropDetail {
                color: #667085;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        title_label = QLabel(title)
        title_label.setObjectName("DropTitle")
        detail_label = QLabel(detail)
        detail_label.setObjectName("DropDetail")
        detail_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(detail_label)
        layout.addStretch(1)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._drop_payload(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        payload = self._drop_payload(event.mimeData())
        if payload is None:
            return
        kind, value = payload
        self.dropped.emit(kind, value)
        event.acceptProposedAction()

    def _drop_payload(self, mime: QMimeData) -> tuple[str, object] | None:
        return parse_transfer_drop_payload(mime)


class RemoteFileTreeItem(QTreeWidgetItem):
    def __lt__(self, other: QTreeWidgetItem) -> bool:
        column = self.treeWidget().sortColumn() if self.treeWidget() is not None else 0
        self_entry = self.data(0, Qt.ItemDataRole.UserRole)
        other_entry = other.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(self_entry, RemoteDirectoryEntry) and isinstance(other_entry, RemoteDirectoryEntry):
            if self_entry.is_directory != other_entry.is_directory:
                return self_entry.is_directory
            if column == 1:
                return self_entry.modified_time < other_entry.modified_time
            if column == 2:
                return self_entry.size < other_entry.size
            return self_entry.name.lower() < other_entry.name.lower()
        return super().__lt__(other)


class RemoteFileList(QTreeWidget):
    dropped = Signal(str, object)

    def __init__(self, drag_kind: str | None = None, accepts_drops: bool = False) -> None:
        super().__init__()
        self.drag_kind = drag_kind
        self.external_file_provider: Callable[[str, str], Path | None] | None = None
        self.setDragEnabled(drag_kind is not None)
        self.setAcceptDrops(accepts_drops)
        self.setMinimumHeight(220)
        self.setColumnCount(3)
        self.setHeaderLabels(["Name", "Modified", "Size"])
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(True)
        self.setItemsExpandable(False)
        self.setIndentation(0)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        style = self.style()
        self._directory_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.setStyleSheet(
            """
            QTreeWidget {
                background: #ffffff;
                border: 1px solid #d7dbe2;
                border-radius: 4px;
                gridline-color: #eef0f3;
            }
            QTreeWidget::item {
                min-height: 24px;
                padding: 2px 4px;
            }
            QTreeWidget::item:alternate {
                background: #fbfcfe;
            }
            QTreeWidget::item:selected {
                background: #dceee8;
                color: #20242a;
            }
            QHeaderView::section {
                background: #f8fafc;
                border: 0;
                border-bottom: 1px solid #d7dbe2;
                padding: 5px 6px;
                color: #4b5563;
                font-weight: 600;
            }
            """
        )

    def set_entries(self, entries: list[RemoteDirectoryEntry]) -> None:
        self.setSortingEnabled(False)
        self.clear()
        for entry in entries:
            if entry.is_directory:
                values = [entry.name, format_timestamp(entry.modified_time), ""]
            else:
                values = [entry.name, format_timestamp(entry.modified_time), format_file_size(entry.size)]
            item = RemoteFileTreeItem(values)
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            item.setIcon(0, self._directory_icon if entry.is_directory else self._file_icon)
            item.setToolTip(0, entry.path)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.addTopLevelItem(item)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    def selected_entry(self) -> RemoteDirectoryEntry | None:
        item = self.currentItem()
        if item is None:
            return None
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        return entry if isinstance(entry, RemoteDirectoryEntry) else None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_kind is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        entry = self.selected_entry()
        if entry is None or entry.is_directory:
            return
        mime, local_path = self._build_drag_payload(entry)
        drag = QDrag(self)
        drag.setMimeData(mime)
        if local_path is None:
            drag.exec(Qt.DropAction.CopyAction)
            return
        drag.exec(Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
        if local_path.exists():
            local_path.unlink(missing_ok=True)

    def _build_drag_mime(self, entry: RemoteDirectoryEntry) -> QMimeData:
        return self._build_drag_payload(entry)[0]

    def _build_drag_payload(self, entry: RemoteDirectoryEntry) -> tuple[QMimeData, Path | None]:
        mime = QMimeData()
        local_path: Path | None = None
        if self.drag_kind == "source_remote":
            mime.setData(REMOTE_SOURCE_MIME, entry.path.encode("utf-8"))
            mime.setText(f"dsmonitor-source:{entry.path}")
        elif self.drag_kind == "target_remote":
            mime.setData(REMOTE_TARGET_MIME, entry.path.encode("utf-8"))
            mime.setText(f"dsmonitor-target:{entry.path}")
        else:
            mime.setText(entry.path)
        if self.external_file_provider is not None and self.drag_kind is not None:
            local_path = self.external_file_provider(self.drag_kind, entry.path)
            if local_path is not None and local_path.is_file():
                mime.setUrls([QUrl.fromLocalFile(str(local_path))])
                mime.setData(
                    'application/x-qt-windows-mime;value="Preferred DropEffect"',
                    (2).to_bytes(4, byteorder="little"),
                )
            else:
                local_path = None
        return mime, local_path

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._drop_payload(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._drop_payload(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        payload = self._drop_payload(event.mimeData())
        if payload is None:
            return
        kind, value = payload
        self.dropped.emit(kind, value)
        event.acceptProposedAction()

    def _drop_payload(self, mime: QMimeData) -> tuple[str, object] | None:
        return parse_transfer_drop_payload(mime)


class RemoteFilePickerDialog(QDialog):
    def __init__(self, settings: AppSettings, start_directory: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._selected_file = ""
        self.setWindowTitle("Select Source File")
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        path_row = QHBoxLayout()
        self.current_directory_edit = QLineEdit()
        self.current_directory_edit.setReadOnly(True)
        self.up_button = QPushButton("Up")
        self.refresh_button = QPushButton("Refresh")
        path_row.addWidget(self.current_directory_edit)
        path_row.addWidget(self.up_button)
        path_row.addWidget(self.refresh_button)

        self.entries_list = QListWidget()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        layout.addLayout(path_row)
        layout.addWidget(self.entries_list)
        layout.addWidget(self.buttons)

        self.up_button.clicked.connect(self._go_up)
        self.refresh_button.clicked.connect(self._refresh)
        self.entries_list.itemDoubleClicked.connect(self._open_item)
        self.buttons.accepted.connect(self._accept_selected)
        self.buttons.rejected.connect(self.reject)

        self._load_directory(start_directory)

    def selected_file(self) -> str:
        return self._selected_file

    def _load_directory(self, directory: str) -> None:
        self.current_directory_edit.setText(directory)
        self.entries_list.clear()
        try:
            entries = list_remote_directory(
                self.settings.source_host,
                self.settings.source_user,
                self.settings.source_password,
                self.settings.source_key_path,
                directory,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Browse Failed", str(exc))
            return

        for entry in entries:
            label = f"[{entry.name}]" if entry.is_directory else entry.name
            self.entries_list.addItem(label)
            item = self.entries_list.item(self.entries_list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, entry)

    @Slot()
    def _go_up(self) -> None:
        current = self.current_directory_edit.text().strip() or "/"
        parent = str(PurePosixPath(current).parent)
        if not parent or parent == ".":
            parent = "/"
        self._load_directory(parent)

    @Slot()
    def _refresh(self) -> None:
        self._load_directory(self.current_directory_edit.text().strip() or "/tftpboot")

    @Slot(object)
    def _open_item(self, item: object) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RemoteDirectoryEntry):
            return
        if entry.is_directory:
            self._load_directory(entry.path)
            return
        self._selected_file = entry.path
        self.accept()

    @Slot()
    def _accept_selected(self) -> None:
        item = self.entries_list.currentItem()
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RemoteDirectoryEntry):
            return
        if entry.is_directory:
            self._load_directory(entry.path)
            return
        self._selected_file = entry.path
        self.accept()


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


class TransferController(QObject):
    progress_received = Signal(int, int)
    finished = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self, work: Callable[[Callable[[int, int], None]], str]) -> bool:
        if self._active:
            return False
        self._active = True
        worker = threading.Thread(target=self._run, args=(work,), daemon=True)
        worker.start()
        return True

    def _run(self, work: Callable[[Callable[[int, int], None]], str]) -> None:
        try:
            message = work(self.progress_received.emit)
        except Exception as exc:
            self.finished.emit(False, str(exc))
            return
        self.finished.emit(True, message)

    @Slot(bool, str)
    def mark_finished(self, _success: bool, _message: str) -> None:
        self._active = False


class RemoteDirectoryListController(QObject):
    finished = Signal(int, bool, str, object, str)

    def start(
        self,
        request_id: int,
        is_source: bool,
        host: str,
        username: str,
        password: str,
        key_path: str,
        directory: str,
    ) -> None:
        worker = threading.Thread(
            target=self._run,
            args=(request_id, is_source, host, username, password, key_path, directory),
            daemon=True,
        )
        worker.start()

    def _run(
        self,
        request_id: int,
        is_source: bool,
        host: str,
        username: str,
        password: str,
        key_path: str,
        directory: str,
    ) -> None:
        try:
            entries = list_remote_directory(host, username, password, key_path, directory)
        except Exception as exc:
            self._emit_finished(request_id, is_source, directory, [], str(exc))
            return
        self._emit_finished(request_id, is_source, directory, entries, "")

    def _emit_finished(
        self,
        request_id: int,
        is_source: bool,
        directory: str,
        entries: list[RemoteDirectoryEntry],
        error: str,
    ) -> None:
        try:
            self.finished.emit(request_id, is_source, directory, entries, error)
        except RuntimeError:
            pass


class MainWindow(QMainWindow):
    def __init__(self, settings_store: SettingsStore, manifest_store: ManifestStore) -> None:
        super().__init__()
        self.settings_store = settings_store
        self.manifest_store = manifest_store
        self.settings = self.settings_store.load()
        self.reverse_manifest_store = ManifestStore(self.settings.app_data_dir / "reverse-manifest.json")
        self.active_monitor_direction = self.settings.monitor_direction
        self._auto_start_proxies_on_launch = self.settings.app_data_dir == Path.home() / "Documents" / "dsmonitor"
        self.proxy_config_path = proxy_config_path(self.settings.app_data_dir)
        self.proxy_config = load_proxy_config(self.proxy_config_path)
        self.proxy_manager = ProxyProcessManager(self.settings.app_data_dir)
        self.selected_proxy_index: int | None = None
        self.forward_controller = SyncController(self._build_service)
        self.reverse_controller = SyncController(self._build_service)
        self.transfer_controller = TransferController()
        self.directory_list_controller = RemoteDirectoryListController()
        self._directory_list_request_ids = {True: 0, False: 0}
        self._quit_requested = False
        self.compact_alert = CompactTrayAlert()
        self.forward_controller.event_received.connect(lambda event: self._handle_event(event, "forward"))
        self.forward_controller.cycle_finished.connect(lambda result: self._handle_cycle_finished(result, "forward"))
        self.reverse_controller.event_received.connect(lambda event: self._handle_event(event, "reverse"))
        self.reverse_controller.cycle_finished.connect(lambda result: self._handle_cycle_finished(result, "reverse"))
        self.transfer_controller.progress_received.connect(self._set_transfer_progress)
        self.transfer_controller.finished.connect(self.transfer_controller.mark_finished)
        self.transfer_controller.finished.connect(self._handle_transfer_finished)
        self.directory_list_controller.finished.connect(self._handle_transfer_list_finished)
        self.setWindowIcon(create_dsmonitor_icon())
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_tray_icon()
        self._load_settings_into_form()
        if self._auto_start_proxies_on_launch:
            QTimer.singleShot(500, self._auto_start_enabled_proxies)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self._hide_to_tray()
            return
        super().changeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._quit_requested:
            event.ignore()
            self._hide_to_tray()
            return
        self.forward_controller.stop()
        self.reverse_controller.stop()
        self.proxy_manager.stop_all()
        self.tray_icon.hide()
        self.compact_alert.hide()
        super().closeEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._window_drop_payload(event) is not None:
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._window_drop_payload(event) is not None:
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        drop_target = self._window_drop_target(event)
        if drop_target is None:
            super().dropEvent(event)
            return
        payload, target = drop_target
        kind, value = payload
        if target == "source":
            self._handle_source_drop(kind, value)
        elif target == "target":
            self._handle_target_drop(kind, value)
        else:
            if kind == "local_files":
                self._handle_target_drop(kind, value)
            else:
                self._handle_local_drop(kind, value)
        event.acceptProposedAction()

    def _window_drop_target(
        self,
        event: QDragMoveEvent | QDropEvent | QDragEnterEvent,
    ) -> tuple[tuple[str, object], str] | None:
        payload = self._window_drop_payload(event)
        if payload is None:
            return None
        receiver = self.childAt(event.position().toPoint())
        if receiver is not None:
            if self.transfer_source_panel.isAncestorOf(receiver) or self.transfer_source_panel is receiver:
                return payload, "source"
            if self.transfer_target_panel.isAncestorOf(receiver) or self.transfer_target_panel is receiver:
                return payload, "target"
        return payload, "local"

    def _window_drop_payload(self, event: QDragMoveEvent | QDropEvent | QDragEnterEvent) -> tuple[str, object] | None:
        if not hasattr(self, "tabs") or self.tabs.tabText(self.tabs.currentIndex()) != "Transfer":
            return None
        payload = parse_transfer_drop_payload(event.mimeData())
        if payload is None:
            return None
        receiver = self.childAt(event.position().toPoint())
        if receiver is not None and (
            self.transfer_source_list.isAncestorOf(receiver)
            or self.transfer_source_list is receiver
            or self.transfer_target_list.isAncestorOf(receiver)
            or self.transfer_target_list is receiver
            or self.transfer_local_drop_zone.isAncestorOf(receiver)
            or self.transfer_local_drop_zone is receiver
        ):
            return None
        return payload

    def _build_ui(self) -> None:
        self.setWindowTitle("dsmonitor")
        self.resize(1040, 620)
        self.setMinimumSize(860, 520)
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f4f6fa;
                color: #172033;
                font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI";
                font-size: 10pt;
            }
            QWidget#Panel {
                background: #ffffff;
                border: 1px solid #d9e0ea;
                border-radius: 8px;
            }
            QLabel#SectionTitle {
                color: #162033;
                font-size: 10.5pt;
                font-weight: 700;
                background: transparent;
            }
            QLabel#FieldLabel {
                color: #526071;
                background: transparent;
            }
            QLabel#AppTitle {
                color: #111827;
                font-size: 14pt;
                font-weight: 700;
                background: transparent;
            }
            QTabWidget::pane {
                border: 1px solid #d9e0ea;
                border-radius: 8px;
                background: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background: #eef2f7;
                border: 1px solid #d9e0ea;
                border-bottom: 0;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
                padding: 7px 14px;
                margin-right: 3px;
                color: #536174;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #172033;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background: #f8fafc;
                color: #243044;
            }
            QLineEdit, QSpinBox {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 8px;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border-color: #3b82f6;
            }
            QComboBox {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 5px 8px;
            }
            QComboBox:editable {
                background: #ffffff;
            }
            QComboBox::drop-down {
                border: 0;
                width: 26px;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0;
                height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #4b5563;
                margin-right: 8px;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                selection-background-color: #e0f2fe;
                selection-color: #172033;
            }
            QLineEdit#StateValue {
                background: #f8fafc;
                border: 0;
                padding: 3px 4px;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 7px 13px;
                color: #172033;
            }
            QPushButton:enabled:hover {
                background: #f8fbff;
                border-color: #60a5fa;
            }
            QPushButton:enabled:pressed {
                background: #eaf3ff;
            }
            QPushButton#PrimaryButton {
                background: #2563eb;
                border-color: #2563eb;
                color: #ffffff;
                font-weight: 600;
            }
            QPushButton#PrimaryButton:enabled:hover {
                background: #1d4ed8;
                border-color: #1d4ed8;
            }
            QPushButton#NavButton {
                background: transparent;
                border-color: transparent;
                color: #334155;
                padding: 6px 10px;
            }
            QPushButton#NavButton:enabled:hover {
                background: #e8eef7;
                border-color: #d8e0ec;
            }
            QPushButton:disabled {
                color: #94a3b8;
                background: #eef2f7;
                border-color: #d7dee9;
            }
            QProgressBar {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                background: #eef2f7;
                text-align: center;
                min-height: 20px;
            }
            QProgressBar::chunk {
                background: #2f7d68;
                border-radius: 5px;
            }
            QTreeWidget {
                background: #ffffff;
                alternate-background-color: #f8fafc;
                border: 1px solid #d9e0ea;
                border-radius: 6px;
                gridline-color: #edf1f6;
            }
            QTreeWidget::item {
                min-height: 26px;
                padding: 2px 5px;
            }
            QTreeWidget::item:selected {
                background: #dbeafe;
                color: #172033;
            }
            QHeaderView::section {
                background: #f8fafc;
                border: 0;
                border-bottom: 1px solid #d9e0ea;
                padding: 6px 7px;
                color: #475569;
                font-weight: 700;
            }
            QCheckBox {
                background: transparent;
                color: #334155;
                spacing: 7px;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid #94a3b8;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #2563eb;
                border-color: #2563eb;
            }
            """
        )

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(18, 14, 18, 16)
        root_layout.setSpacing(10)

        nav_layout = QHBoxLayout()
        app_title = QLabel("dsmonitor")
        app_title.setObjectName("AppTitle")
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("NavButton")
        nav_layout.addWidget(app_title)
        nav_layout.addStretch(1)
        nav_layout.addWidget(self.settings_button)

        status_panel = QWidget()
        status_panel.setObjectName("Panel")
        status_panel_layout = QVBoxLayout(status_panel)
        status_panel_layout.setContentsMargins(16, 12, 16, 14)
        status_panel_title = QLabel("Status")
        status_panel_title.setObjectName("SectionTitle")
        status_layout = QGridLayout()
        self.activity_value = QLabel("Idle")
        self.summary_value = QLabel("Scanned 0 | Changed 0 | Synced 0 | Failed 0")
        self.status_message_value = QLabel("Ready")
        self.status_message_value.setWordWrap(True)
        self.source_file_state_value = QLineEdit("-")
        self.local_file_state_value = QLineEdit("-")
        self.destination_file_state_value = QLineEdit("-")
        for value_label in [
            self.source_file_state_value,
            self.local_file_state_value,
            self.destination_file_state_value,
        ]:
            value_label.setReadOnly(True)
            value_label.setObjectName("StateValue")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        for label_text, row in [
            ("Now", 0),
            ("10.55.2.104 source", 1),
            ("Local cache", 2),
            ("10.71.1.3 target", 3),
            ("Summary", 4),
            ("Progress", 5),
        ]:
            label = QLabel(label_text)
            label.setObjectName("FieldLabel")
            status_layout.addWidget(label, row, 0)
        status_layout.addWidget(self.status_message_value, 0, 1)
        status_layout.addWidget(self.source_file_state_value, 1, 1)
        status_layout.addWidget(self.local_file_state_value, 2, 1)
        status_layout.addWidget(self.destination_file_state_value, 3, 1)
        status_layout.addWidget(self.summary_value, 4, 1)
        status_layout.addWidget(self.progress_bar, 5, 1)
        status_layout.setColumnStretch(1, 1)
        status_panel_layout.addWidget(status_panel_title)
        status_panel_layout.addLayout(status_layout)
        self.status_panel = status_panel

        monitor_panel = QWidget()
        monitor_panel.setObjectName("Panel")
        monitor_panel_layout = QVBoxLayout(monitor_panel)
        monitor_panel_layout.setContentsMargins(16, 12, 16, 14)
        monitor_panel_title = QLabel("Monitor")
        monitor_panel_title.setObjectName("SectionTitle")
        monitor_layout = QFormLayout()
        self.source_file_edit = QLineEdit()
        self.source_file_edit.setPlaceholderText("/tftpboot/a.x, /tftpboot/b.x, or /tftpboot")
        self.source_file_completion_model = QStringListModel(self)
        self.source_file_completer = QCompleter(self.source_file_completion_model, self)
        self.source_file_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.source_file_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.source_file_edit.setCompleter(self.source_file_completer)
        self.source_file_completion_timer = QTimer(self)
        self.source_file_completion_timer.setSingleShot(True)
        self.source_file_completion_timer.setInterval(300)
        self.source_file_completion_timer.timeout.connect(self._refresh_source_file_completions)
        self.browse_source_file_button = QPushButton("Browse...")
        source_file_row = QWidget()
        source_file_layout = QHBoxLayout(source_file_row)
        source_file_layout.setContentsMargins(0, 0, 0, 0)
        source_file_layout.addWidget(self.source_file_edit)
        source_file_layout.addWidget(self.browse_source_file_button)
        monitor_layout.addRow("Monitor Path", source_file_row)
        monitor_browser_title = QLabel("10.55.2.104 Source Browser")
        monitor_browser_title.setObjectName("SectionTitle")
        monitor_path_row = QWidget()
        monitor_path_layout = QHBoxLayout(monitor_path_row)
        monitor_path_layout.setContentsMargins(0, 0, 0, 0)
        self.monitor_source_directory_edit = self._build_remote_path_combo("/tftpboot", True)
        self.monitor_source_up_button = QPushButton("Up")
        self.monitor_source_use_folder_button = QPushButton("Use Folder")
        self.monitor_source_refresh_button = QPushButton("Refresh")
        self.monitor_source_up_button.setFixedWidth(72)
        self.monitor_source_use_folder_button.setFixedWidth(104)
        self.monitor_source_refresh_button.setFixedWidth(96)
        monitor_path_layout.addWidget(self.monitor_source_directory_edit, 1)
        monitor_path_layout.addWidget(self.monitor_source_up_button)
        monitor_path_layout.addWidget(self.monitor_source_use_folder_button)
        monitor_path_layout.addWidget(self.monitor_source_refresh_button)
        self.monitor_source_list = RemoteFileList(drag_kind=None)
        monitor_panel_layout.addWidget(monitor_panel_title)
        monitor_panel_layout.addLayout(monitor_layout)
        monitor_panel_layout.addWidget(monitor_browser_title)
        monitor_panel_layout.addWidget(monitor_path_row)
        monitor_panel_layout.addWidget(self.monitor_source_list, 1)

        self.settings_fields_container = QWidget()
        self.settings_fields_container.setObjectName("Panel")
        advanced_layout = QVBoxLayout(self.settings_fields_container)
        advanced_layout.setContentsMargins(16, 12, 16, 14)
        settings_layout = QFormLayout()
        self.source_host_edit = QLineEdit()
        self.source_user_edit = QLineEdit()
        self.source_password_edit = QLineEdit()
        self.source_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.source_key_path_edit = QLineEdit()
        self.browse_source_key_button = QPushButton("Browse...")
        source_key_row = QWidget()
        source_key_layout = QHBoxLayout(source_key_row)
        source_key_layout.setContentsMargins(0, 0, 0, 0)
        source_key_layout.addWidget(self.source_key_path_edit)
        source_key_layout.addWidget(self.browse_source_key_button)
        self.destination_host_edit = QLineEdit()
        self.destination_user_edit = QLineEdit()
        self.destination_password_edit = QLineEdit()
        self.destination_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.destination_key_path_edit = QLineEdit()
        self.browse_destination_key_button = QPushButton("Browse...")
        destination_key_row = QWidget()
        destination_key_layout = QHBoxLayout(destination_key_row)
        destination_key_layout.setContentsMargins(0, 0, 0, 0)
        destination_key_layout.addWidget(self.destination_key_path_edit)
        destination_key_layout.addWidget(self.browse_destination_key_button)
        self.destination_path_edit = QLineEdit()
        self.poll_interval_spinbox = QSpinBox()
        self.poll_interval_spinbox.setRange(1, 3600)
        self.local_cache_edit = QLineEdit()
        settings_layout.addRow("Source Host", self.source_host_edit)
        settings_layout.addRow("Source User", self.source_user_edit)
        settings_layout.addRow("Source Password", self.source_password_edit)
        settings_layout.addRow("Source Key", source_key_row)
        settings_layout.addRow("Destination Host", self.destination_host_edit)
        settings_layout.addRow("Destination User", self.destination_user_edit)
        settings_layout.addRow("Destination Password", self.destination_password_edit)
        settings_layout.addRow("Destination Key", destination_key_row)
        settings_layout.addRow("Destination Root", self.destination_path_edit)
        settings_layout.addRow("Poll Interval (s)", self.poll_interval_spinbox)
        settings_layout.addRow("Local Cache", self.local_cache_edit)
        advanced_layout.addLayout(settings_layout)
        settings_actions_layout = QHBoxLayout()
        self.save_settings_button = QPushButton("Save Settings")
        self.save_settings_button.setObjectName("PrimaryButton")
        self.settings_saved_label = QLabel("")
        settings_actions_layout.addWidget(self.save_settings_button)
        settings_actions_layout.addWidget(self.settings_saved_label, 1)
        settings_actions_layout.addStretch(1)
        advanced_layout.addLayout(settings_actions_layout)

        controls_layout = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("PrimaryButton")
        self.stop_button = QPushButton("Stop")
        self.rescan_button = QPushButton("Sync Now")
        self.open_folder_button = QPushButton("Cache Folder")
        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.stop_button)
        controls_layout.addWidget(self.rescan_button)
        controls_layout.addWidget(self.open_folder_button)
        controls_layout.addStretch(1)

        monitor_tab = QWidget()
        monitor_tab_layout = QVBoxLayout(monitor_tab)
        monitor_tab_layout.setContentsMargins(0, 0, 0, 0)
        monitor_tab_layout.setSpacing(8)
        monitor_tab_layout.addWidget(monitor_panel, 1)
        monitor_tab_layout.addLayout(controls_layout)

        self.tabs = QTabWidget()
        self.tabs.addTab(monitor_tab, "Monitor")
        self.tabs.addTab(self._build_reverse_monitor_tab(), "Reverse Monitor")
        self.tabs.addTab(self._build_transfer_tab(), "Transfer")
        self.tabs.addTab(self._build_proxy_tab(), "Proxy")
        self.tabs.addTab(self.settings_fields_container, "Settings")
        self.tabs.currentChanged.connect(self._handle_tab_changed)

        root_layout.addLayout(nav_layout)
        root_layout.addWidget(self.tabs, 1)
        root_layout.addWidget(self.status_panel)
        self.root_layout = root_layout
        self._apply_tab_layout_policy(self.tabs.currentIndex())

        self.start_button.clicked.connect(self._start_monitoring)
        self.stop_button.clicked.connect(self._stop_monitoring)
        self.rescan_button.clicked.connect(self._rescan_now)
        self.open_folder_button.clicked.connect(self._open_local_folder)
        self.browse_source_file_button.clicked.connect(self._browse_source_file)
        self.settings_button.clicked.connect(self._show_settings_tab)
        self.save_settings_button.clicked.connect(self._save_settings)
        self.browse_source_key_button.clicked.connect(self._browse_source_key)
        self.browse_destination_key_button.clicked.connect(self._browse_destination_key)
        self.source_file_edit.textEdited.connect(self._schedule_source_file_completion)
        self.monitor_source_refresh_button.clicked.connect(self._refresh_monitor_source_list)
        self.monitor_source_up_button.clicked.connect(lambda: self._go_monitor_source_directory())
        self.monitor_source_use_folder_button.clicked.connect(self._use_monitor_source_folder)
        self.monitor_source_directory_edit.lineEdit().returnPressed.connect(self._refresh_monitor_source_list)
        self.monitor_source_directory_edit.lineEdit().textEdited.connect(
            lambda _text: self._show_transfer_path_completions(self.monitor_source_directory_edit)
        )
        self.proxy_status_timer = QTimer(self)
        self.proxy_status_timer.setInterval(2000)
        self.proxy_status_timer.timeout.connect(self._refresh_proxy_rows)
        self.proxy_status_timer.start()
        self.monitor_source_list.itemDoubleClicked.connect(self._open_monitor_source_item)
        self._apply_button_icons()
        self._set_forward_running_state(False)
        self._set_reverse_running_state(False)

    def _build_reverse_monitor_tab(self) -> QWidget:
        reverse_tab = QWidget()
        layout = QVBoxLayout(reverse_tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        reverse_panel = QWidget()
        reverse_panel.setObjectName("Panel")
        reverse_panel_layout = QVBoxLayout(reverse_panel)
        reverse_panel_layout.setContentsMargins(16, 12, 16, 14)
        reverse_title = QLabel("Reverse Monitor")
        reverse_title.setObjectName("SectionTitle")

        reverse_form = QFormLayout()
        self.reverse_source_file_edit = QLineEdit()
        self.reverse_source_file_edit.setPlaceholderText("/home/tsl/a.x, /home/tsl/b.x, or /home/tsl")
        self.reverse_source_completion_model = QStringListModel(self)
        self.reverse_source_completer = QCompleter(self.reverse_source_completion_model, self)
        self.reverse_source_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.reverse_source_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.reverse_source_file_edit.setCompleter(self.reverse_source_completer)
        self.reverse_destination_path_edit = QLineEdit()
        self.reverse_destination_path_edit.setPlaceholderText("/tftpboot")

        reverse_source_row = QWidget()
        reverse_source_layout = QHBoxLayout(reverse_source_row)
        reverse_source_layout.setContentsMargins(0, 0, 0, 0)
        reverse_source_layout.addWidget(self.reverse_source_file_edit)
        reverse_form.addRow("Monitor Path", reverse_source_row)
        reverse_form.addRow("Send To 10.55.2.104", self.reverse_destination_path_edit)

        reverse_browser_title = QLabel("10.71.1.3 Source Browser")
        reverse_browser_title.setObjectName("SectionTitle")
        reverse_path_row = QWidget()
        reverse_path_layout = QHBoxLayout(reverse_path_row)
        reverse_path_layout.setContentsMargins(0, 0, 0, 0)
        self.reverse_monitor_directory_edit = self._build_remote_path_combo("/home/tsl", False)
        self.reverse_monitor_up_button = QPushButton("Up")
        self.reverse_monitor_use_folder_button = QPushButton("Use Folder")
        self.reverse_monitor_refresh_button = QPushButton("Refresh")
        self.reverse_monitor_up_button.setFixedWidth(72)
        self.reverse_monitor_use_folder_button.setFixedWidth(104)
        self.reverse_monitor_refresh_button.setFixedWidth(96)
        reverse_path_layout.addWidget(self.reverse_monitor_directory_edit, 1)
        reverse_path_layout.addWidget(self.reverse_monitor_up_button)
        reverse_path_layout.addWidget(self.reverse_monitor_use_folder_button)
        reverse_path_layout.addWidget(self.reverse_monitor_refresh_button)
        self.reverse_monitor_list = RemoteFileList(drag_kind=None)

        reverse_controls_layout = QHBoxLayout()
        self.reverse_start_button = QPushButton("Start")
        self.reverse_start_button.setObjectName("PrimaryButton")
        self.reverse_stop_button = QPushButton("Stop")
        self.reverse_rescan_button = QPushButton("Sync Now")
        self.reverse_controls_cache_button = QPushButton("Cache Folder")
        reverse_controls_layout.addWidget(self.reverse_start_button)
        reverse_controls_layout.addWidget(self.reverse_stop_button)
        reverse_controls_layout.addWidget(self.reverse_rescan_button)
        reverse_controls_layout.addWidget(self.reverse_controls_cache_button)
        reverse_controls_layout.addStretch(1)

        reverse_panel_layout.addWidget(reverse_title)
        reverse_panel_layout.addLayout(reverse_form)
        reverse_panel_layout.addWidget(reverse_browser_title)
        reverse_panel_layout.addWidget(reverse_path_row)
        reverse_panel_layout.addWidget(self.reverse_monitor_list, 1)
        layout.addWidget(reverse_panel, 1)
        layout.addLayout(reverse_controls_layout)

        self.reverse_monitor_refresh_button.clicked.connect(self._refresh_reverse_monitor_list)
        self.reverse_monitor_up_button.clicked.connect(lambda: self._go_reverse_monitor_directory())
        self.reverse_monitor_use_folder_button.clicked.connect(self._use_reverse_monitor_folder)
        self.reverse_monitor_directory_edit.lineEdit().returnPressed.connect(self._refresh_reverse_monitor_list)
        self.reverse_monitor_directory_edit.lineEdit().textEdited.connect(
            lambda _text: self._show_transfer_path_completions(self.reverse_monitor_directory_edit)
        )
        self.reverse_monitor_list.itemDoubleClicked.connect(self._open_reverse_monitor_item)
        self.reverse_start_button.clicked.connect(self._start_reverse_monitoring)
        self.reverse_stop_button.clicked.connect(self._stop_reverse_monitoring)
        self.reverse_rescan_button.clicked.connect(self._rescan_reverse_now)
        self.reverse_controls_cache_button.clicked.connect(self._open_reverse_local_folder)
        return reverse_tab

    def _build_transfer_tab(self) -> QWidget:
        transfer_tab = QWidget()
        layout = QVBoxLayout(transfer_tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        browsers_row = QWidget()
        self.transfer_browsers_row = browsers_row
        browsers_layout = QHBoxLayout(browsers_row)
        browsers_layout.setContentsMargins(0, 0, 0, 0)
        browsers_layout.setSpacing(8)

        source_panel = self._build_remote_browser_panel(
            title="10.55.2.104 Source",
            default_directory="/tftpboot",
            is_source=True,
        )
        self.transfer_source_panel = source_panel
        target_panel = self._build_remote_browser_panel(
            title="10.71.1.3 Target",
            default_directory="/home/tsl",
            is_source=False,
        )
        self.transfer_target_panel = target_panel
        browsers_layout.addWidget(source_panel)
        browsers_layout.addWidget(target_panel)

        self.transfer_local_drop_zone = TransferDropZone(
            "Local",
            "Drag a file from 10.55.2.104 or 10.71.1.3 here to download into the local dsmonitor folder.",
            "local",
        )

        transfer_status_panel = QWidget()
        transfer_status_panel.setObjectName("Panel")
        transfer_status_layout = QVBoxLayout(transfer_status_panel)
        transfer_status_layout.setContentsMargins(16, 12, 16, 14)
        self.transfer_status_value = QLabel("Ready")
        self.transfer_progress_bar = QProgressBar()
        self.transfer_progress_bar.setRange(0, 100)
        self.transfer_progress_bar.setValue(0)
        transfer_status_layout.addWidget(self.transfer_status_value)
        transfer_status_layout.addWidget(self.transfer_progress_bar)

        layout.addWidget(browsers_row, 1)
        layout.addWidget(self.transfer_local_drop_zone)
        layout.addWidget(transfer_status_panel)

        self.transfer_local_drop_zone.dropped.connect(self._handle_local_drop)
        return transfer_tab

    def _build_remote_path_combo(self, default_directory: str, is_source: bool) -> QComboBox:
        path_edit = QComboBox()
        path_edit.setEditable(True)
        path_edit.addItems(self._initial_transfer_directories(default_directory, is_source))
        path_edit.setCurrentText(default_directory)
        path_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        path_edit.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        path_edit.setMinimumContentsLength(24)
        completer = QCompleter(path_edit.model(), path_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        path_edit.setCompleter(completer)
        return path_edit

    def _build_remote_browser_panel(self, title: str, default_directory: str, is_source: bool) -> QWidget:
        panel = QWidget()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 14)
        header = QLabel(title)
        header.setObjectName("SectionTitle")
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_edit = self._build_remote_path_combo(default_directory, is_source)
        up_button = QPushButton("Up")
        refresh_button = QPushButton("Refresh")
        up_button.setFixedWidth(72)
        refresh_button.setFixedWidth(96)
        path_layout.addWidget(path_edit, 1)
        path_layout.addWidget(up_button)
        path_layout.addWidget(refresh_button)
        remote_list = RemoteFileList(
            drag_kind="source_remote" if is_source else "target_remote",
            accepts_drops=True,
        )
        remote_list.external_file_provider = self._prepare_external_remote_drag
        layout.addWidget(header)
        layout.addWidget(path_row)
        layout.addWidget(remote_list, 1)

        if is_source:
            self.transfer_source_directory_edit = path_edit
            self.transfer_source_up_button = up_button
            self.transfer_source_refresh_button = refresh_button
            self.transfer_source_list = remote_list
            remote_list.dropped.connect(self._handle_source_drop)
            refresh_button.clicked.connect(self._refresh_transfer_source_list)
            up_button.clicked.connect(lambda: self._go_transfer_directory(self.transfer_source_directory_edit, True))
            path_edit.lineEdit().returnPressed.connect(self._refresh_transfer_source_list)
            path_edit.lineEdit().textEdited.connect(lambda _text, combo=path_edit: self._show_transfer_path_completions(combo))
            remote_list.itemDoubleClicked.connect(lambda item: self._open_transfer_item(item, True))
        else:
            self.transfer_target_directory_edit = path_edit
            self.transfer_target_up_button = up_button
            self.transfer_target_refresh_button = refresh_button
            self.transfer_target_list = remote_list
            remote_list.dropped.connect(self._handle_target_drop)
            refresh_button.clicked.connect(self._refresh_transfer_target_list)
            up_button.clicked.connect(lambda: self._go_transfer_directory(self.transfer_target_directory_edit, False))
            path_edit.lineEdit().returnPressed.connect(self._refresh_transfer_target_list)
            path_edit.lineEdit().textEdited.connect(lambda _text, combo=path_edit: self._show_transfer_path_completions(combo))
            remote_list.itemDoubleClicked.connect(lambda item: self._open_transfer_item(item, False))
        return panel

    def _build_tray_icon(self) -> None:
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(create_dsmonitor_icon())
        self.tray_icon.setToolTip("dsmonitor - double-click to show")

        tray_menu = QMenu(self)
        self.show_action = tray_menu.addAction("Show")
        self.show_action.triggered.connect(self._restore_from_tray)
        self.exit_action = tray_menu.addAction("Exit")
        self.exit_action.triggered.connect(self._exit_from_tray)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._handle_tray_activated)
        self.tray_icon.show()

    def _hide_to_tray(self) -> None:
        was_visible = self.isVisible()
        self.hide()
        if was_visible:
            self._show_tray_alert("dsmonitor", "Still running in the tray")

    @Slot()
    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    @Slot(object)
    def _handle_tray_activated(self, reason: object) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    @Slot()
    def _exit_from_tray(self) -> None:
        self._quit_requested = True
        self.tray_icon.hide()
        QApplication.quit()

    def _show_tray_alert(self, title: str, message: str) -> None:
        self.compact_alert.show_alert(title, message)

    def _refresh_monitor_source_list(self) -> None:
        settings = self._current_settings_from_form()
        directory = self._current_transfer_directory(self.monitor_source_directory_edit, "/tftpboot")
        try:
            entries = list_remote_directory(
                settings.source_host,
                settings.source_user,
                settings.source_password,
                settings.source_key_path,
                directory,
            )
        except Exception as exc:
            self._set_status_message(f"{settings.source_host} source list unavailable: {exc}")
            return
        self._set_transfer_directory(self.monitor_source_directory_edit, directory, "/tftpboot")
        self._add_child_directory_choices(self.monitor_source_directory_edit, entries, "/tftpboot")
        self.monitor_source_list.set_entries(entries)
        self._set_status_message(f"Listed {len(entries)} item(s) from {settings.source_host}:{directory}")

    def _refresh_reverse_monitor_list(self) -> None:
        settings = self._current_settings_from_form()
        directory = self._current_transfer_directory(self.reverse_monitor_directory_edit, settings.destination_path)
        try:
            entries = list_remote_directory(
                settings.destination_host,
                settings.destination_user,
                settings.destination_password,
                settings.destination_key_path,
                directory,
            )
        except Exception as exc:
            self._set_status_message(f"{settings.destination_host} reverse source list unavailable: {exc}")
            return
        self._set_transfer_directory(self.reverse_monitor_directory_edit, directory, settings.destination_path)
        self._add_child_directory_choices(self.reverse_monitor_directory_edit, entries, settings.destination_path)
        self.reverse_monitor_list.set_entries(entries)
        self._set_status_message(f"Listed {len(entries)} item(s) from {settings.destination_host}:{directory}")

    def _go_monitor_source_directory(self) -> None:
        current = self.monitor_source_directory_edit.currentText().strip() or "/"
        self._set_transfer_directory(self.monitor_source_directory_edit, parent_remote_directory(current), "/")
        self._refresh_monitor_source_list()

    def _go_reverse_monitor_directory(self) -> None:
        current = self.reverse_monitor_directory_edit.currentText().strip() or "/"
        self._set_transfer_directory(self.reverse_monitor_directory_edit, parent_remote_directory(current), "/")
        self._refresh_reverse_monitor_list()

    def _use_monitor_source_folder(self) -> None:
        directory = self._current_transfer_directory(self.monitor_source_directory_edit, "/tftpboot")
        self.source_file_edit.setText(append_monitor_path(self.source_file_edit.text(), directory))
        self._set_status_message(f"Monitoring source folder selected: {directory}")

    def _use_reverse_monitor_folder(self) -> None:
        directory = self._current_transfer_directory(self.reverse_monitor_directory_edit, self.settings.destination_path)
        self.reverse_source_file_edit.setText(append_monitor_path(self.reverse_source_file_edit.text(), directory))
        self._set_status_message(f"Reverse monitoring folder selected: {directory}")

    def _open_monitor_source_item(self, item: QTreeWidgetItem) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RemoteDirectoryEntry):
            return
        if entry.is_directory:
            self._set_transfer_directory(self.monitor_source_directory_edit, entry.path, "/tftpboot")
            self._refresh_monitor_source_list()
            return
        self.source_file_edit.setText(append_monitor_path(self.source_file_edit.text(), entry.path))
        self._set_status_message(f"Monitoring source selected: {entry.path}")

    def _open_reverse_monitor_item(self, item: QTreeWidgetItem) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RemoteDirectoryEntry):
            return
        if entry.is_directory:
            self._set_transfer_directory(self.reverse_monitor_directory_edit, entry.path, self.settings.destination_path)
            self._refresh_reverse_monitor_list()
            return
        self.reverse_source_file_edit.setText(append_monitor_path(self.reverse_source_file_edit.text(), entry.path))
        self._set_status_message(f"Reverse monitoring source selected: {entry.path}")

    def _initial_transfer_directories(self, default_directory: str, is_source: bool) -> list[str]:
        common = [default_directory]
        if is_source:
            common.extend(["/tftpboot", "/home/wei.li", "/tmp", "/"])
        else:
            common.extend(["/home/tsl", "/home", "/tmp", "/"])
        choices: list[str] = []
        for directory in common:
            normalized = normalize_remote_directory(directory, default_directory)
            if normalized not in choices:
                choices.append(normalized)
        return choices

    def _current_transfer_directory(self, path_edit: QComboBox, fallback: str) -> str:
        return normalize_remote_directory(path_edit.currentText(), fallback)

    def _set_transfer_directory(self, path_edit: QComboBox, directory: str, fallback: str = "/") -> None:
        normalized = normalize_remote_directory(directory, fallback)
        if path_edit.findText(normalized) < 0:
            path_edit.insertItem(0, normalized)
        path_edit.setCurrentText(normalized)

    def _add_transfer_directory_choice(self, path_edit: QComboBox, directory: str, fallback: str = "/") -> None:
        normalized = normalize_remote_directory(directory, fallback)
        if path_edit.findText(normalized) < 0:
            path_edit.addItem(normalized)

    def _add_child_directory_choices(
        self,
        path_edit: QComboBox,
        entries: list[RemoteDirectoryEntry],
        fallback: str,
    ) -> None:
        for entry in entries:
            if entry.is_directory:
                self._add_transfer_directory_choice(path_edit, entry.path, fallback)

    def _show_transfer_path_completions(self, path_edit: QComboBox) -> None:
        if path_edit.count() > 0 and path_edit.completer() is not None:
            path_edit.completer().complete()

    def _refresh_transfer_source_list(self) -> None:
        self._start_transfer_list_refresh(True)

    def _refresh_transfer_target_list(self) -> None:
        self._start_transfer_list_refresh(False)

    def _start_transfer_list_refresh(self, is_source: bool) -> None:
        settings = self._current_settings_from_form()
        if is_source:
            path_edit = self.transfer_source_directory_edit
            fallback = "/tftpboot"
            host = settings.source_host
            username = settings.source_user
            password = settings.source_password
            key_path = settings.source_key_path
        else:
            path_edit = self.transfer_target_directory_edit
            fallback = settings.destination_path
            host = settings.destination_host
            username = settings.destination_user
            password = settings.destination_password
            key_path = settings.destination_key_path
        directory = self._current_transfer_directory(path_edit, fallback)
        self._set_transfer_directory(path_edit, directory, fallback)
        self._directory_list_request_ids[is_source] += 1
        request_id = self._directory_list_request_ids[is_source]
        self.transfer_status_value.setText(f"Listing {host}:{directory}...")
        self.directory_list_controller.start(
            request_id,
            is_source,
            host,
            username,
            password,
            key_path,
            directory,
        )

    @Slot(int, bool, str, object, str)
    def _handle_transfer_list_finished(
        self,
        request_id: int,
        is_source: bool,
        directory: str,
        entries: object,
        error: str,
    ) -> None:
        if request_id != self._directory_list_request_ids[is_source]:
            return
        settings = self._current_settings_from_form()
        if is_source:
            path_edit = self.transfer_source_directory_edit
            remote_list = self.transfer_source_list
            fallback = "/tftpboot"
            host = settings.source_host
        else:
            path_edit = self.transfer_target_directory_edit
            remote_list = self.transfer_target_list
            fallback = settings.destination_path
            host = settings.destination_host
        if error:
            self.transfer_status_value.setText(f"Transfer list unavailable for {host}:{directory}: {error}")
            return
        remote_entries = entries if isinstance(entries, list) else []
        self._set_transfer_directory(path_edit, directory, fallback)
        self._add_child_directory_choices(path_edit, remote_entries, fallback)
        remote_list.set_entries(remote_entries)
        self.transfer_status_value.setText(f"Listed {len(remote_entries)} item(s) from {host}:{directory}")

    def _refresh_transfer_lists_safely(self) -> None:
        self._refresh_transfer_source_list()
        self._refresh_transfer_target_list()

    def _go_transfer_directory(self, path_edit: QComboBox, is_source: bool) -> None:
        current = path_edit.currentText().strip() or "/"
        self._set_transfer_directory(path_edit, parent_remote_directory(current), "/")
        if is_source:
            self._refresh_transfer_source_list()
        else:
            self._refresh_transfer_target_list()

    def _open_transfer_item(self, item: QTreeWidgetItem, is_source: bool) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RemoteDirectoryEntry) or not entry.is_directory:
            return
        if is_source:
            self._set_transfer_directory(self.transfer_source_directory_edit, entry.path, "/tftpboot")
            self._refresh_transfer_source_list()
        else:
            self._set_transfer_directory(self.transfer_target_directory_edit, entry.path, self.settings.destination_path)
            self._refresh_transfer_target_list()

    @Slot(str, object)
    def _handle_local_drop(self, kind: str, payload: object) -> None:
        if kind not in {"source_remote", "target_remote"} or not isinstance(payload, str):
            self.transfer_status_value.setText("Drop a remote file here to download.")
            return
        settings = self._current_settings_from_form()
        remote_path = payload
        local_directory = settings.local_cache_dir
        self._reset_transfer_progress()
        self.transfer_status_value.setText(f"Downloading {PurePosixPath(remote_path).name}...")

        def work(progress: Callable[[int, int], None]) -> str:
            downloader = download_manual_file if kind == "source_remote" else download_target_manual_file
            result = downloader(settings, remote_path, local_directory, progress)
            return f"Downloaded {Path(result.local_path).name} -> {result.local_path}"

        self._start_transfer(work)

    @Slot(str, object)
    def _handle_source_drop(self, kind: str, payload: object) -> None:
        settings = self._current_settings_from_form()
        source_directory = self._current_transfer_directory(self.transfer_source_directory_edit, "/tftpboot")
        self._reset_transfer_progress()
        if kind == "local_files" and isinstance(payload, list):
            local_paths = [Path(local_path) for local_path in payload]
            self.transfer_status_value.setText(f"Uploading {len(local_paths)} file(s) to {settings.source_host}...")

            def work(progress: Callable[[int, int], None]) -> str:
                uploaded = []
                for local_path in local_paths:
                    upload_source_manual_file(
                        settings,
                        local_path,
                        source_directory,
                        progress,
                    )
                    uploaded.append(local_path.name)
                return f"Uploaded {', '.join(uploaded)} -> {settings.source_host}:{source_directory}"

            self._start_transfer(work)
            return
        if kind == "target_remote" and isinstance(payload, str):
            target_remote_path = payload
            temp_directory = settings.app_data_dir / "transfer"
            self.transfer_status_value.setText(f"Copying {PurePosixPath(target_remote_path).name} to {settings.source_host}...")

            def work(progress: Callable[[int, int], None]) -> str:
                result = copy_destination_to_source(
                    settings,
                    target_remote_path,
                    source_directory,
                    temp_directory,
                    progress,
                )
                return f"Copied {PurePosixPath(target_remote_path).name} -> {settings.source_host}:{result.remote_path}"

            self._start_transfer(work)
            return
        self.transfer_status_value.setText("Drop local files or a 10.71.1.3 file here.")

    @Slot(str, object)
    def _handle_target_drop(self, kind: str, payload: object) -> None:
        settings = self._current_settings_from_form()
        target_directory = self._current_transfer_directory(self.transfer_target_directory_edit, settings.destination_path)
        self._reset_transfer_progress()
        if kind == "local_files" and isinstance(payload, list):
            local_paths = [Path(local_path) for local_path in payload]
            self.transfer_status_value.setText(f"Uploading {len(local_paths)} file(s)...")

            def work(progress: Callable[[int, int], None]) -> str:
                uploaded = []
                for local_path in local_paths:
                    upload_manual_file(
                        settings,
                        local_path,
                        target_directory,
                        progress,
                    )
                    uploaded.append(local_path.name)
                return f"Uploaded {', '.join(uploaded)} -> {target_directory}"

            self._start_transfer(work)
            return
        if kind == "source_remote" and isinstance(payload, str):
            source_remote_path = payload
            temp_directory = settings.app_data_dir / "transfer"
            self.transfer_status_value.setText(f"Copying {PurePosixPath(source_remote_path).name}...")

            def work(progress: Callable[[int, int], None]) -> str:
                result = copy_source_to_destination(
                    settings,
                    source_remote_path,
                    target_directory,
                    temp_directory,
                    progress,
                )
                return f"Copied {PurePosixPath(source_remote_path).name} -> {result.remote_path}"

            self._start_transfer(work)
            return
        self.transfer_status_value.setText("Drop local files or a 10.55.2.104 source file here.")

    def _prepare_external_remote_drag(self, kind: str, remote_path: str) -> Path | None:
        settings = self._current_settings_from_form()
        local_directory = settings.local_cache_dir / "drag-staging"
        downloader = download_manual_file if kind == "source_remote" else download_target_manual_file
        self._reset_transfer_progress()
        self.transfer_status_value.setText(f"Preparing {PurePosixPath(remote_path).name} for move-out...")
        QApplication.processEvents()

        def progress(done: int, total: int) -> None:
            self._set_transfer_progress(done, total)
            QApplication.processEvents()

        try:
            result = downloader(settings, remote_path, local_directory, progress)
        except Exception as exc:
            self.transfer_status_value.setText(f"Drag-out download failed: {exc}")
            return None
        self.transfer_status_value.setText(f"Ready to move {Path(result.local_path).name} outside dsmonitor")
        return result.local_path

    def _start_transfer(self, work: Callable[[Callable[[int, int], None]], str]) -> None:
        self._set_transfer_controls_enabled(False)
        if not self.transfer_controller.start(work):
            self._set_transfer_controls_enabled(True)
            self.transfer_status_value.setText("Transfer already running.")
            return

    @Slot(bool, str)
    def _handle_transfer_finished(self, success: bool, message: str) -> None:
        self._set_transfer_controls_enabled(True)
        if success:
            self.transfer_status_value.setText(message)
            self.transfer_progress_bar.setValue(self.transfer_progress_bar.maximum())
        else:
            self.transfer_status_value.setText(f"Transfer failed: {message}")

    def _set_transfer_controls_enabled(self, enabled: bool) -> None:
        for widget in [
            self.transfer_local_drop_zone,
            self.transfer_source_list,
            self.transfer_target_list,
            self.transfer_source_refresh_button,
            self.transfer_target_refresh_button,
            self.transfer_source_up_button,
            self.transfer_target_up_button,
        ]:
            widget.setEnabled(enabled)

    @Slot(int)
    def _handle_tab_changed(self, index: int) -> None:
        tab_name = self.tabs.tabText(index)
        self.status_panel.setVisible(tab_name in {"Monitor", "Reverse Monitor"})
        if tab_name == "Transfer":
            self._refresh_transfer_lists_safely()

    def _apply_tab_layout_policy(self, index: int) -> None:
        root_layout = self.root_layout
        tab_layout_index = root_layout.indexOf(self.tabs)
        root_layout.setStretch(tab_layout_index, 1)

    def _reset_transfer_progress(self) -> None:
        self._transfer_progress_started_at = time.monotonic()
        self.transfer_progress_bar.setRange(0, 100)
        self.transfer_progress_bar.setValue(0)
        self.transfer_progress_bar.setFormat("%p%")

    def _set_transfer_progress(self, transferred: int, total: int) -> None:
        if total <= 0:
            self.transfer_progress_bar.setRange(0, 0)
            return
        self.transfer_progress_bar.setRange(0, total)
        self.transfer_progress_bar.setValue(transferred)
        elapsed = max(time.monotonic() - getattr(self, "_transfer_progress_started_at", time.monotonic()), 0.001)
        self.transfer_progress_bar.setFormat(f"%p% - {format_transfer_rate(transferred / elapsed)}")

    def _refresh_proxy_rows(self) -> None:
        if not hasattr(self, "proxy_tree"):
            return
        self.proxy_manager.reap()
        selected_name = self._selected_proxy().name if self._selected_proxy() else ""
        signals_were_blocked = self.proxy_tree.blockSignals(True)
        try:
            self.proxy_tree.clear()
            for index, tunnel in enumerate(self.proxy_config.tunnels):
                item = QTreeWidgetItem(
                    [
                        tunnel.name,
                        "yes" if tunnel.enabled else "no",
                        f"{tunnel.local_host}:{tunnel.local_port}",
                        f"{tunnel.remote_host}:{tunnel.remote_port}",
                        f"{tunnel.jump_user}@{tunnel.jump_host}",
                        "password" if has_password(tunnel.name) else "key/agent",
                        self.proxy_manager.status(tunnel),
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, index)
                self.proxy_tree.addTopLevelItem(item)
                if tunnel.name == selected_name:
                    self.proxy_tree.setCurrentItem(item)
        finally:
            self.proxy_tree.blockSignals(signals_were_blocked)

    def _selected_proxy(self) -> ProxyTunnel | None:
        if self.selected_proxy_index is None:
            return None
        if self.selected_proxy_index < 0 or self.selected_proxy_index >= len(self.proxy_config.tunnels):
            return None
        return self.proxy_config.tunnels[self.selected_proxy_index]

    @Slot()
    def _select_proxy_from_tree(self) -> None:
        selected_items = self.proxy_tree.selectedItems()
        if not selected_items:
            self.selected_proxy_index = None
            return
        item = selected_items[0]
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(index, int):
            return
        self.selected_proxy_index = index
        tunnel = self.proxy_config.tunnels[index]
        self.proxy_enabled_check.setChecked(tunnel.enabled)
        self.proxy_name_edit.setText(tunnel.name)
        self.proxy_local_host_edit.setText(tunnel.local_host)
        self.proxy_local_port_spin.setValue(tunnel.local_port)
        self.proxy_remote_host_edit.setText(tunnel.remote_host)
        self.proxy_remote_port_spin.setValue(tunnel.remote_port)
        self.proxy_jump_user_edit.setText(tunnel.jump_user)
        self.proxy_jump_host_edit.setText(tunnel.jump_host)
        self.proxy_password_edit.setText("")
        if has_password(tunnel.name):
            self.proxy_status_label.setText(f"Password is saved for {tunnel.name}. Leave password blank to keep it.")

    def _read_proxy_form(self) -> ProxyTunnel:
        name = self.proxy_name_edit.text().strip()
        if not name:
            raise ValueError("Name is required.")
        local_host = self.proxy_local_host_edit.text().strip()
        remote_host = self.proxy_remote_host_edit.text().strip()
        jump_user = self.proxy_jump_user_edit.text().strip()
        jump_host = self.proxy_jump_host_edit.text().strip()
        if not local_host or not remote_host or not jump_user or not jump_host:
            raise ValueError("Local host, remote host, jump user, and jump host are required.")
        return ProxyTunnel(
            name=name,
            local_host=local_host,
            local_port=self.proxy_local_port_spin.value(),
            remote_host=remote_host,
            remote_port=self.proxy_remote_port_spin.value(),
            jump_user=jump_user,
            jump_host=jump_host,
            enabled=self.proxy_enabled_check.isChecked(),
        )

    @Slot()
    def _new_proxy(self) -> None:
        self.selected_proxy_index = None
        self.proxy_tree.clearSelection()
        self.proxy_enabled_check.setChecked(True)
        self.proxy_name_edit.setText("")
        self.proxy_local_host_edit.setText("127.0.0.1")
        self.proxy_local_port_spin.setValue(13389)
        self.proxy_remote_host_edit.setText("10.71.20.231")
        self.proxy_remote_port_spin.setValue(3389)
        self.proxy_jump_user_edit.setText("tsl")
        self.proxy_jump_host_edit.setText("10.71.1.3")
        self.proxy_password_edit.setText("")
        self.proxy_status_label.setText(f"Config: {self.proxy_config_path}")

    @Slot()
    def _save_proxy(self) -> None:
        try:
            tunnel = self._read_proxy_form()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid proxy", str(exc))
            return
        duplicate = duplicate_tunnel_name(self.proxy_config, tunnel.name, self.selected_proxy_index)
        if duplicate is not None:
            self.proxy_status_label.setText(f"Duplicate tunnel name: {duplicate}")
            QMessageBox.warning(self, "Duplicate tunnel name", f"Tunnel name '{duplicate}' already exists.")
            return
        endpoint_duplicate = duplicate_local_endpoint(
            self.proxy_config,
            tunnel.local_host,
            tunnel.local_port,
            self.selected_proxy_index,
        )
        if endpoint_duplicate is not None:
            self.proxy_status_label.setText(f"Duplicate local port: {tunnel.local_host}:{tunnel.local_port}")
            QMessageBox.warning(
                self,
                "Duplicate local port",
                f"{tunnel.local_host}:{tunnel.local_port} is already used by '{endpoint_duplicate}'.",
            )
            return
        tunnels = list(self.proxy_config.tunnels)
        old_tunnel = tunnels[self.selected_proxy_index] if self.selected_proxy_index is not None else None
        if self.selected_proxy_index is None:
            tunnels.append(tunnel)
            self.selected_proxy_index = len(tunnels) - 1
        else:
            tunnels[self.selected_proxy_index] = tunnel
        password = self.proxy_password_edit.text()
        if password:
            write_password(tunnel.name, tunnel.jump_user, password)
            self.proxy_password_edit.setText("")
            if old_tunnel and old_tunnel.name != tunnel.name:
                delete_password(old_tunnel.name)
        elif old_tunnel and old_tunnel.name != tunnel.name:
            old_password = read_password(old_tunnel.name)
            if old_password is not None:
                write_password(tunnel.name, tunnel.jump_user, old_password)
                delete_password(old_tunnel.name)
        self.proxy_config = ProxyConfig(tunnels=tunnels)
        save_proxy_config(self.proxy_config_path, self.proxy_config)
        self.proxy_status_label.setText(f"Saved {self.proxy_config_path}")
        self._refresh_proxy_rows()

    @Slot()
    def _delete_proxy(self) -> None:
        tunnel = self._selected_proxy()
        if tunnel is None:
            return
        self.proxy_manager.stop(tunnel)
        delete_password(tunnel.name)
        tunnels = list(self.proxy_config.tunnels)
        del tunnels[self.selected_proxy_index]
        self.selected_proxy_index = None
        self.proxy_config = ProxyConfig(tunnels=tunnels)
        save_proxy_config(self.proxy_config_path, self.proxy_config)
        self.proxy_status_label.setText(f"Deleted {tunnel.name}")
        self._new_proxy()
        self._refresh_proxy_rows()

    @Slot()
    def _start_selected_proxy(self) -> None:
        tunnel = self._selected_proxy()
        if tunnel is None:
            return
        self.proxy_status_label.setText(self.proxy_manager.start(tunnel))
        self._refresh_proxy_rows()

    @Slot()
    def _stop_selected_proxy(self) -> None:
        tunnel = self._selected_proxy()
        if tunnel is None:
            return
        self.proxy_status_label.setText(self.proxy_manager.stop(tunnel))
        self._refresh_proxy_rows()

    @Slot()
    def _start_all_proxies(self) -> None:
        started = 0
        for tunnel in self.proxy_config.tunnels:
            if tunnel.enabled:
                self.proxy_manager.start(tunnel)
                started += 1
        self.proxy_status_label.setText(f"Started {started} enabled proxy session(s)")
        self._refresh_proxy_rows()

    @Slot()
    def _auto_start_enabled_proxies(self) -> None:
        if not hasattr(self, "proxy_status_label"):
            return
        started = 0
        for tunnel in self.proxy_config.tunnels:
            if tunnel.enabled:
                self.proxy_manager.start(tunnel)
                started += 1
        if started:
            self.proxy_status_label.setText(f"Auto-started {started} enabled proxy session(s)")
            self._refresh_proxy_rows()

    @Slot()
    def _stop_all_proxies(self) -> None:
        self.proxy_manager.stop_all()
        self.proxy_status_label.setText("Stopped all proxy sessions")
        self._refresh_proxy_rows()

    @Slot()
    def _install_proxy_startup(self) -> None:
        path = proxy_startup_batch_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_proxy_startup_batch_content(current_exe_path()), encoding="utf-8")
        self.proxy_status_label.setText(f"Installed Startup launcher {path}")

    @Slot()
    def _remove_proxy_startup(self) -> None:
        path = proxy_startup_batch_path()
        if path.exists():
            path.unlink()
            self.proxy_status_label.setText(f"Removed Startup launcher {path}")
        else:
            self.proxy_status_label.setText("Startup launcher is not installed")

    @Slot()
    def _open_proxy_log(self) -> None:
        path = proxy_log_path(self.settings.app_data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _build_service(
        self,
        settings: AppSettings,
        event_callback: Callable[[SyncEvent], None],
    ) -> SshSyncService:
        manifest_store = self.reverse_manifest_store if settings.monitor_direction == "reverse" else self.manifest_store
        return SshSyncService(
            settings=settings,
            manifest_store=manifest_store,
            event_callback=event_callback,
        )

    def _load_settings_into_form(self) -> None:
        self.source_file_edit.setText(format_monitor_paths(self.settings.source_files))
        self.reverse_source_file_edit.setText(format_monitor_paths(self.settings.reverse_source_files))
        self.reverse_destination_path_edit.setText(self.settings.reverse_destination_path)
        self._set_transfer_directory(self.transfer_source_directory_edit, "/tftpboot", "/tftpboot")
        self._set_transfer_directory(self.transfer_target_directory_edit, self.settings.destination_path, "/home/tsl")
        self._set_transfer_directory(self.reverse_monitor_directory_edit, self.settings.destination_path, "/home/tsl")
        self.source_host_edit.setText(self.settings.source_host)
        self.source_user_edit.setText(self.settings.source_user)
        self.source_password_edit.setText(self.settings.source_password)
        self.source_key_path_edit.setText(self.settings.source_key_path)
        self.destination_host_edit.setText(self.settings.destination_host)
        self.destination_user_edit.setText(self.settings.destination_user)
        self.destination_password_edit.setText(self.settings.destination_password)
        self.destination_key_path_edit.setText(self.settings.destination_key_path)
        self.destination_path_edit.setText(self.settings.destination_path)
        self.poll_interval_spinbox.setValue(self.settings.poll_interval_seconds)
        self.local_cache_edit.setText(str(self.settings.local_cache_dir))

    def _current_settings_from_form(self) -> AppSettings:
        local_cache_dir = Path(self.local_cache_edit.text().strip())
        source_files = parse_monitor_paths(self.source_file_edit.text())
        reverse_source_files = parse_monitor_paths(self.reverse_source_file_edit.text())
        return AppSettings(
            source_host=self.source_host_edit.text().strip(),
            source_user=self.source_user_edit.text().strip(),
            source_password=self.source_password_edit.text(),
            source_key_path=self.source_key_path_edit.text().strip(),
            source_directories=[],
            source_files=source_files,
            destination_host=self.destination_host_edit.text().strip(),
            destination_user=self.destination_user_edit.text().strip(),
            destination_password=self.destination_password_edit.text(),
            destination_key_path=self.destination_key_path_edit.text().strip(),
            destination_path=self.destination_path_edit.text().strip(),
            reverse_source_files=reverse_source_files,
            reverse_destination_path=self.reverse_destination_path_edit.text().strip(),
            monitor_direction=self.active_monitor_direction,
            poll_interval_seconds=self.poll_interval_spinbox.value(),
            local_cache_dir=local_cache_dir,
            app_data_dir=self.settings.app_data_dir,
        )

    def _forward_monitor_settings_from_form(self) -> AppSettings:
        settings = self._current_settings_from_form()
        settings.monitor_direction = "forward"
        return settings

    def _reverse_monitor_settings_from_form(self) -> AppSettings:
        settings = self._current_settings_from_form()
        reverse_paths = parse_monitor_paths(self.reverse_source_file_edit.text())
        settings.monitor_direction = "reverse"
        settings.source_host, settings.destination_host = settings.destination_host, settings.source_host
        settings.source_user, settings.destination_user = settings.destination_user, settings.source_user
        settings.source_password, settings.destination_password = settings.destination_password, settings.source_password
        settings.source_key_path, settings.destination_key_path = settings.destination_key_path, settings.source_key_path
        settings.source_files = reverse_paths
        settings.source_directories = []
        settings.destination_path = self.reverse_destination_path_edit.text().strip()
        settings.local_cache_dir = settings.local_cache_dir / "reverse"
        return settings

    def _persist_form_settings(self, require_source_file: bool = True) -> AppSettings | None:
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
        if require_source_file and not parse_monitor_paths(self.source_file_edit.text()):
            QMessageBox.warning(self, "Missing Source File", "Choose one or more source files or folders.")
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

    def _persist_reverse_form_settings(self) -> AppSettings | None:
        settings = self._persist_form_settings(require_source_file=False)
        if settings is None:
            return None
        if not parse_monitor_paths(self.reverse_source_file_edit.text()):
            QMessageBox.warning(self, "Missing Reverse Monitor Path", "Choose one or more 10.71.1.3 files or folders.")
            return None
        if not self.reverse_destination_path_edit.text().strip():
            QMessageBox.warning(self, "Missing Reverse Destination", "Choose a 10.55.2.104 destination folder.")
            return None
        return self._reverse_monitor_settings_from_form()

    @Slot()
    def _save_settings(self) -> None:
        settings = self._persist_form_settings(require_source_file=False)
        if settings is None:
            return
        self.settings_saved_label.setText(f"Saved to {self.settings_store.path}")

    def _connection_settings_from_form(self) -> AppSettings | None:
        if not self.source_host_edit.text().strip():
            QMessageBox.warning(self, "Missing Source Host", "Source host cannot be empty.")
            return None
        if not self.source_user_edit.text().strip():
            QMessageBox.warning(self, "Missing Source User", "Source user cannot be empty.")
            return None
        return self._current_settings_from_form()

    def _set_running_state(self, running: bool) -> None:
        self._set_forward_running_state(running)
        self._set_reverse_running_state(running)

    def _set_forward_running_state(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _set_reverse_running_state(self, running: bool) -> None:
        self.reverse_start_button.setEnabled(not running)
        self.reverse_stop_button.setEnabled(running)

    def _apply_button_icons(self) -> None:
        style = self.style()
        self.settings_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.browse_source_file_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.browse_source_key_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.browse_destination_key_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.monitor_source_up_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        self.monitor_source_use_folder_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.monitor_source_refresh_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.reverse_monitor_up_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        self.reverse_monitor_use_folder_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.reverse_monitor_refresh_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.start_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.stop_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.rescan_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.open_folder_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.reverse_start_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.reverse_stop_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.reverse_rescan_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.reverse_controls_cache_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))

    @Slot()
    def _start_monitoring(self) -> None:
        settings = self._persist_form_settings()
        if settings is None:
            return
        self.active_monitor_direction = "forward"
        settings = self._forward_monitor_settings_from_form()
        self.settings.monitor_direction = "forward"
        self.settings_store.save(self.settings)
        self.forward_controller.start(settings)
        self._set_forward_running_state(True)
        self._set_status_message("Monitoring started")

    @Slot()
    def _start_reverse_monitoring(self) -> None:
        settings = self._persist_reverse_form_settings()
        if settings is None:
            return
        self.active_monitor_direction = "reverse"
        self.settings.monitor_direction = "reverse"
        self.settings_store.save(self.settings)
        self.reverse_controller.start(settings)
        self._set_reverse_running_state(True)
        self._set_status_message("Reverse monitoring started")

    @Slot()
    def _stop_monitoring(self) -> None:
        self.forward_controller.stop()
        self._set_forward_running_state(False)
        self.activity_value.setText("Stopped")
        self._set_status_message("Monitoring stopped")

    @Slot()
    def _stop_reverse_monitoring(self) -> None:
        self.reverse_controller.stop()
        self._set_reverse_running_state(False)
        self.activity_value.setText("Stopped")
        self._set_status_message("Reverse monitoring stopped")

    @Slot()
    def _rescan_now(self) -> None:
        settings = self._persist_form_settings()
        if settings is None:
            return
        self.active_monitor_direction = "forward"
        settings = self._forward_monitor_settings_from_form()
        self.settings.monitor_direction = "forward"
        self.settings_store.save(self.settings)
        self.forward_controller.trigger_rescan(settings)
        self._set_status_message("Manual sync requested")

    @Slot()
    def _rescan_reverse_now(self) -> None:
        settings = self._persist_reverse_form_settings()
        if settings is None:
            return
        self.active_monitor_direction = "reverse"
        self.settings.monitor_direction = "reverse"
        self.settings_store.save(self.settings)
        self.reverse_controller.trigger_rescan(settings)
        self._set_status_message("Reverse manual sync requested")

    @Slot()
    def _open_local_folder(self) -> None:
        settings = self._persist_form_settings(require_source_file=False)
        if settings is None:
            return
        settings.local_cache_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(settings.local_cache_dir)))

    @Slot()
    def _open_reverse_local_folder(self) -> None:
        settings = self._persist_form_settings(require_source_file=False)
        if settings is None:
            return
        reverse_cache_dir = settings.local_cache_dir / "reverse"
        reverse_cache_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(reverse_cache_dir)))

    @Slot()
    def _browse_source_key(self) -> None:
        self._browse_key_path(self.source_key_path_edit)

    @Slot()
    def _browse_destination_key(self) -> None:
        self._browse_key_path(self.destination_key_path_edit)

    def _browse_key_path(self, target: QLineEdit) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Select SSH Key", str(Path.home() / ".ssh"))
        if selected:
            target.setText(selected)

    def _monitor_tab_matches(self, direction: str | None) -> bool:
        if direction is None or not hasattr(self, "tabs"):
            return True
        current_tab = self.tabs.tabText(self.tabs.currentIndex())
        return (direction == "forward" and current_tab == "Monitor") or (
            direction == "reverse" and current_tab == "Reverse Monitor"
        )

    def _handle_event(self, event: SyncEvent, direction: str | None = None) -> None:
        if not self._monitor_tab_matches(direction):
            if event.kind == "file_synced" and (not self.isVisible() or self.isMinimized()):
                self._show_tray_alert("dsmonitor", event.message)
            return
        if event.kind == "file_state":
            self._set_file_state(event)
            return
        self.activity_value.setText(event.activity.capitalize())
        self._set_status_message(event.message)
        if event.kind == "scan_started":
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("")
        elif event.kind == "download_progress":
            self._set_progress(
                event.bytes_transferred,
                event.total_bytes,
                f"{event.kind}:{event.current_file}",
            )
        elif event.kind == "upload_progress":
            self._set_progress(
                event.bytes_transferred,
                event.total_bytes,
                f"{event.kind}:{event.current_file}",
            )
        elif event.kind == "file_synced":
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("%p%")
            if not self.isVisible() or self.isMinimized():
                self._show_tray_alert("dsmonitor", event.message)

    def _handle_cycle_finished(self, result: SyncCycleResult, direction: str | None = None) -> None:
        if direction == "forward":
            controller = self.forward_controller
            set_running_state = self._set_forward_running_state
        elif direction == "reverse":
            controller = self.reverse_controller
            set_running_state = self._set_reverse_running_state
        else:
            controller = self.forward_controller
            set_running_state = self._set_forward_running_state
        if not controller.is_running:
            set_running_state(False)
        if not self._monitor_tab_matches(direction):
            return
        self.summary_value.setText(format_status_summary(result))
        if result.activity == "idle":
            self.activity_value.setText("Idle")
            self.progress_bar.setRange(0, 100)
            if result.changed_files == 0:
                self.progress_bar.setValue(0)
                self.progress_bar.setFormat("%p%")
        else:
            self.activity_value.setText("Error")
        if result.last_error:
            self._set_status_message(f"Error: {result.last_error}")
        elif result.failed_files:
            self._set_status_message("Sync finished with errors")
        elif result.synced_files:
            self._set_status_message(f"Synced {result.synced_files} file(s)")
        elif result.changed_files == 0:
            self._set_status_message("No changes")

    def _set_status_message(self, message: str) -> None:
        self.status_message_value.setText(message)

    def _set_file_state(self, event: SyncEvent) -> None:
        self.source_file_state_value.setText(
            format_file_state(event.source_path, event.source_modified_time, event.source_size)
        )
        self.local_file_state_value.setText(
            format_file_state(event.local_path, event.local_modified_time, event.local_size)
        )
        self.destination_file_state_value.setText(
            format_file_state(event.destination_path, event.destination_modified_time, event.destination_size)
        )
        self.source_file_state_value.setCursorPosition(0)
        self.local_file_state_value.setCursorPosition(0)
        self.destination_file_state_value.setCursorPosition(0)

    @Slot()
    def _show_settings_tab(self) -> None:
        self.tabs.setCurrentWidget(self.settings_fields_container)

    def _build_proxy_tab(self) -> QWidget:
        proxy_tab = QWidget()
        layout = QVBoxLayout(proxy_tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        list_panel = QWidget()
        list_panel.setObjectName("Panel")
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(16, 12, 16, 14)
        title = QLabel("SSH Proxy Sessions")
        title.setObjectName("SectionTitle")
        self.proxy_tree = QTreeWidget()
        self.proxy_tree.setColumnCount(7)
        self.proxy_tree.setHeaderLabels(["Name", "Auto", "Local", "Remote", "Jump", "Auth", "Status"])
        self.proxy_tree.setRootIsDecorated(False)
        self.proxy_tree.setAlternatingRowColors(True)
        self.proxy_tree.setUniformRowHeights(True)
        self.proxy_tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        proxy_header = self.proxy_tree.header()
        proxy_header.setStretchLastSection(False)
        proxy_header.setMinimumSectionSize(44)
        for column in range(7):
            proxy_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate([120, 52, 138, 148, 128, 86, 86]):
            self.proxy_tree.setColumnWidth(column, width)
        self.proxy_tree.itemSelectionChanged.connect(self._select_proxy_from_tree)

        proxy_controls = QHBoxLayout()
        self.proxy_start_button = QPushButton("Start")
        self.proxy_stop_button = QPushButton("Stop")
        self.proxy_start_all_button = QPushButton("Start All")
        self.proxy_stop_all_button = QPushButton("Stop All")
        self.proxy_install_startup_button = QPushButton("Install Startup")
        self.proxy_remove_startup_button = QPushButton("Remove Startup")
        self.proxy_open_log_button = QPushButton("Open Log")
        self.proxy_start_button.setObjectName("PrimaryButton")
        self.proxy_start_all_button.setObjectName("PrimaryButton")
        for button in [
            self.proxy_start_button,
            self.proxy_stop_button,
            self.proxy_start_all_button,
            self.proxy_stop_all_button,
            self.proxy_install_startup_button,
            self.proxy_remove_startup_button,
            self.proxy_open_log_button,
        ]:
            proxy_controls.addWidget(button)

        list_layout.addWidget(title)
        list_layout.addWidget(self.proxy_tree, 1)
        list_layout.addLayout(proxy_controls)

        form_panel = QWidget()
        form_panel.setObjectName("Panel")
        form_layout = QVBoxLayout(form_panel)
        form_layout.setContentsMargins(16, 12, 16, 14)
        form_title = QLabel("Tunnel")
        form_title.setObjectName("SectionTitle")
        proxy_form = QFormLayout()
        self.proxy_enabled_check = QCheckBox("Enabled for startup")
        self.proxy_name_edit = QLineEdit()
        self.proxy_local_host_edit = QLineEdit("127.0.0.1")
        self.proxy_local_port_spin = QSpinBox()
        self.proxy_local_port_spin.setRange(1, 65535)
        self.proxy_local_port_spin.setValue(13389)
        self.proxy_remote_host_edit = QLineEdit("10.71.20.231")
        self.proxy_remote_port_spin = QSpinBox()
        self.proxy_remote_port_spin.setRange(1, 65535)
        self.proxy_remote_port_spin.setValue(3389)
        self.proxy_jump_user_edit = QLineEdit("tsl")
        self.proxy_jump_host_edit = QLineEdit("10.71.1.3")
        self.proxy_password_edit = QLineEdit()
        self.proxy_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        proxy_form.addRow("", self.proxy_enabled_check)
        proxy_form.addRow("Name", self.proxy_name_edit)
        proxy_form.addRow("Local host", self.proxy_local_host_edit)
        proxy_form.addRow("Local port", self.proxy_local_port_spin)
        proxy_form.addRow("Remote host", self.proxy_remote_host_edit)
        proxy_form.addRow("Remote port", self.proxy_remote_port_spin)
        proxy_form.addRow("Jump user", self.proxy_jump_user_edit)
        proxy_form.addRow("Jump host", self.proxy_jump_host_edit)
        proxy_form.addRow("Password", self.proxy_password_edit)

        form_buttons = QHBoxLayout()
        self.proxy_new_button = QPushButton("New")
        self.proxy_save_button = QPushButton("Save")
        self.proxy_delete_button = QPushButton("Delete")
        self.proxy_save_button.setObjectName("PrimaryButton")
        form_buttons.addWidget(self.proxy_new_button)
        form_buttons.addWidget(self.proxy_save_button)
        form_buttons.addWidget(self.proxy_delete_button)

        self.proxy_status_label = QLabel(f"Config: {self.proxy_config_path}")
        self.proxy_status_label.setWordWrap(True)
        form_layout.addWidget(form_title)
        form_layout.addLayout(proxy_form)
        form_layout.addLayout(form_buttons)
        form_layout.addStretch(1)

        content_layout.addWidget(list_panel, 1)
        content_layout.addWidget(form_panel)

        layout.addWidget(content, 1)
        layout.addWidget(self.proxy_status_label)

        self.proxy_start_button.clicked.connect(self._start_selected_proxy)
        self.proxy_stop_button.clicked.connect(self._stop_selected_proxy)
        self.proxy_start_all_button.clicked.connect(self._start_all_proxies)
        self.proxy_stop_all_button.clicked.connect(self._stop_all_proxies)
        self.proxy_install_startup_button.clicked.connect(self._install_proxy_startup)
        self.proxy_remove_startup_button.clicked.connect(self._remove_proxy_startup)
        self.proxy_open_log_button.clicked.connect(self._open_proxy_log)
        self.proxy_new_button.clicked.connect(self._new_proxy)
        self.proxy_save_button.clicked.connect(self._save_proxy)
        self.proxy_delete_button.clicked.connect(self._delete_proxy)

        self._new_proxy()
        self._refresh_proxy_rows()
        return proxy_tab

    def _resize_to_content(self) -> None:
        self.adjustSize()

    def _set_progress(self, transferred: int, total: int, key: str = "") -> None:
        if total <= 0:
            self.progress_bar.setRange(0, 0)
            return
        now = time.monotonic()
        if getattr(self, "_progress_key", "") != key or transferred == 0:
            self._progress_key = key
            self._progress_started_at = now
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(transferred)
        elapsed = max(now - getattr(self, "_progress_started_at", now), 0.001)
        self.progress_bar.setFormat(f"%p% - {format_transfer_rate(transferred / elapsed)}")

    @Slot()
    def _schedule_source_file_completion(self) -> None:
        self.source_file_completion_timer.start()

    @Slot()
    def _refresh_source_file_completions(self) -> None:
        source_host = self.source_host_edit.text().strip()
        source_user = self.source_user_edit.text().strip()
        if not source_host or not source_user:
            return

        existing_prefix, _active_path = monitor_completion_parts(self.source_file_edit.text())
        directory, prefix = remote_completion_context(self.source_file_edit.text())
        try:
            entries = list_remote_directory(
                source_host,
                source_user,
                self.source_password_edit.text(),
                self.source_key_path_edit.text().strip(),
                directory,
            )
        except Exception as exc:
            self._set_status_message(f"Autocomplete unavailable: {exc}")
            return

        completions = [f"{existing_prefix}{completion}" for completion in build_remote_path_completions(entries, prefix)]
        self.source_file_completion_model.setStringList(completions)
        if completions:
            self.source_file_completer.complete()

    @Slot()
    def _browse_source_file(self) -> None:
        settings = self._connection_settings_from_form()
        if settings is None:
            return
        dialog = RemoteFilePickerDialog(
            settings,
            initial_remote_directory(self.source_file_edit.text()),
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_file():
            self.source_file_edit.setText(append_monitor_path(self.source_file_edit.text(), dialog.selected_file()))
