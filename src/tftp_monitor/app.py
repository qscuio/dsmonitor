from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .gui import MainWindow
from .manifest_store import ManifestStore
from .settings_store import SettingsStore


def main() -> int:
    app_data_dir = Path.home() / "AppData" / "Local" / "TftpMonitor"
    settings_store = SettingsStore(app_data_dir / "settings.json")
    manifest_store = ManifestStore(app_data_dir / "manifest.json")

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(settings_store=settings_store, manifest_store=manifest_store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
