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
        default_settings = AppSettings.default(self.path.parent)
        legacy_source_path = payload.pop("source_path", None)
        payload.setdefault("source_password", default_settings.source_password)
        payload.setdefault("source_key_path", default_settings.source_key_path)
        payload.setdefault("source_directories", default_settings.source_directories)
        payload.setdefault("source_files", [legacy_source_path] if legacy_source_path else default_settings.source_files)
        payload.setdefault("destination_key_path", default_settings.destination_key_path)
        payload.setdefault("reverse_source_files", default_settings.reverse_source_files)
        payload.setdefault("reverse_destination_path", default_settings.reverse_destination_path)
        payload.setdefault("monitor_direction", default_settings.monitor_direction)
        payload["local_cache_dir"] = Path(payload["local_cache_dir"])
        payload["app_data_dir"] = Path(payload["app_data_dir"])
        return AppSettings(**payload)

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(settings)
        payload["local_cache_dir"] = str(settings.local_cache_dir)
        payload["app_data_dir"] = str(settings.app_data_dir)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
