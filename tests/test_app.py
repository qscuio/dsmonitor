from __future__ import annotations

import time
import uuid

from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from tftp_monitor.app import SingleInstanceGuard


def test_single_instance_guard_notifies_existing_instance() -> None:
    app = QApplication.instance() or QApplication([])
    server_name = f"dsmonitor-test-{uuid.uuid4().hex}"
    activations: list[str] = []
    primary = SingleInstanceGuard(server_name)
    secondary = SingleInstanceGuard(server_name)

    try:
        assert primary.acquire_or_notify_existing()
        primary.set_activate_callback(lambda: activations.append("activate"))

        assert not secondary.acquire_or_notify_existing()

        deadline = time.monotonic() + 1.0
        while not activations and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

        assert activations == ["activate"]
    finally:
        primary.server.close()
        secondary.server.close()
        QLocalServer.removeServer(server_name)
