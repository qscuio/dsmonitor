from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import AppSettings


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings.default(self.path.parent)

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload.setdefault("source_directories", AppSettings.default(self.path.parent).source_directories)
        payload.pop("source_path", None)
        payload["local_cache_dir"] = Path(payload["local_cache_dir"])
        payload["app_data_dir"] = Path(payload["app_data_dir"])
        return AppSettings(**payload)

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(settings)
        payload["local_cache_dir"] = str(settings.local_cache_dir)
        payload["app_data_dir"] = str(settings.app_data_dir)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
