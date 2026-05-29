from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import FileRecord


class ManifestStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, FileRecord]:
        if not self.path.exists():
            return {}

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {key: FileRecord(**value) for key, value in raw.items()}

    def save(self, records: dict[str, FileRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: asdict(record) for key, record in records.items()}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
