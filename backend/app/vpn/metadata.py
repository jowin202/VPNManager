"""Local metadata per VPN (not the source of truth for OPNsense objects, no secrets)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class MetadataStore:
    def __init__(self, data_dir: Path):
        self._path = data_dir / "vpns.json"
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except FileNotFoundError:
            return {}

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self._path)

    def all(self) -> dict:
        with self._lock:
            return self._read()

    def get(self, name: str) -> dict:
        return self.all().get(name, {})

    def put(self, name: str, record: dict) -> None:
        with self._lock:
            data = self._read()
            data[name] = record
            self._write(data)

    def delete(self, name: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(name, None) is not None:
                self._write(data)
