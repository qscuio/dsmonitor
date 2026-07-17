from __future__ import annotations

import sys
import ctypes
import argparse
import os
from datetime import datetime
from pathlib import Path
from collections.abc import Callable

from .manifest_store import ManifestStore
from .settings_store import SettingsStore


SINGLE_INSTANCE_SERVER_NAME = "liwei.dsmonitor.single-instance"


class SingleInstanceGuard:
    def __init__(self, server_name: str = SINGLE_INSTANCE_SERVER_NAME) -> None:
        from PySide6.QtNetwork import QLocalServer

        self.server_name = server_name
        self.server = QLocalServer()
        self._activate_callback: Callable[[], None] | None = None
        self.server.newConnection.connect(self._handle_new_connection)

    def acquire_or_notify_existing(self) -> bool:
        if self._notify_existing_instance():
            return False

        from PySide6.QtNetwork import QLocalServer

        QLocalServer.removeServer(self.server_name)
        if self.server.listen(self.server_name):
            return True

        return not self._notify_existing_instance()

    def set_activate_callback(self, callback: Callable[[], None]) -> None:
        self._activate_callback = callback

    def _notify_existing_instance(self) -> bool:
        from PySide6.QtNetwork import QLocalSocket

        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(150):
            return False
        socket.write(b"activate\n")
        socket.flush()
        socket.waitForBytesWritten(150)
        socket.disconnectFromServer()
        return True

    def _handle_new_connection(self) -> None:
        while self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            if connection is not None:
                connection.readAll()
                connection.disconnectFromServer()
                connection.deleteLater()
        if self._activate_callback is not None:
            self._activate_callback()


def _write_proxy_agent_boot_log(app_data_dir: Path, message: str = "proxy agent argument received") -> None:
    app_data_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with (app_data_dir / "proxy-agent.log").open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def main() -> int:
    app_data_dir = Path.home() / "Documents" / "dsmonitor"
    proxy_agent_requested = os.environ.get("DSMONITOR_PROXY_AGENT") == "1" or any(
        "proxy-agent" in argument for argument in sys.argv[1:]
    )
    if sys.argv[1:]:
        _write_proxy_agent_boot_log(app_data_dir, f"argv={sys.argv[1:]}")
    if os.environ.get("DSMONITOR_PROXY_AGENT") == "1":
        _write_proxy_agent_boot_log(app_data_dir, "env DSMONITOR_PROXY_AGENT=1")
    if proxy_agent_requested:
        _write_proxy_agent_boot_log(app_data_dir)
        try:
            from .proxy_tunnel import run_proxy_agent

            run_proxy_agent(app_data_dir)
        except Exception as exc:
            _write_proxy_agent_boot_log(app_data_dir, f"proxy agent failed: {exc!r}")
            raise
        return 0

    parser = argparse.ArgumentParser(description="dsmonitor")
    parser.add_argument("--proxy-agent", action="store_true", help="run configured SSH proxy tunnels")
    args = parser.parse_args()

    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("liwei.dsmonitor.feather")

    if args.proxy_agent:
        try:
            from .proxy_tunnel import run_proxy_agent

            run_proxy_agent(app_data_dir)
        except Exception as exc:
            _write_proxy_agent_boot_log(app_data_dir, f"proxy agent failed: {exc!r}")
            raise
        return 0

    from .gui import MainWindow, create_dsmonitor_icon
    from PySide6.QtWidgets import QApplication

    settings_store = SettingsStore(app_data_dir / "settings.json")
    manifest_store = ManifestStore(app_data_dir / "manifest.json")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(create_dsmonitor_icon())

    single_instance = SingleInstanceGuard()
    if not single_instance.acquire_or_notify_existing():
        return 0

    window = MainWindow(settings_store=settings_store, manifest_store=manifest_store)
    single_instance.set_activate_callback(window._restore_from_tray)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
