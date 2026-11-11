"""Schema discovery.

`GET .../get_item` without a UUID returns an empty record with defaults and,
for selection fields, all options ({"value": ..., "selected": 0/1}). From it
we derive the root key, the real field names and valid option values.
Payloads are checked against this schema before they are sent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .client import OpnsenseClient
from .errors import SchemaError


@dataclass
class ModelSchema:
    path: str
    root_key: str
    defaults: dict

    @classmethod
    def from_response(cls, path: str, data: dict) -> "ModelSchema":
        roots = [k for k, v in data.items() if isinstance(v, dict)]
        if len(roots) != 1:
            raise SchemaError(f"Cannot determine the root key unambiguously (candidates: {roots})", path=path)
        return cls(path=path, root_key=roots[0], defaults=data[roots[0]])

    @property
    def fields(self) -> list[str]:
        return list(self.defaults)

    def has(self, name: str) -> bool:
        return name in self.defaults

    def field(self, *candidates: str) -> str:
        """First candidate that exists in the live schema. Candidates are
        expectations from docs/source code, never a guarantee."""
        for c in candidates:
            if c in self.defaults:
                return c
        raise SchemaError(
            f"None of the fields {list(candidates)} exists in the schema of {self.path} "
            f"(available: {', '.join(self.fields)})",
            path=self.path,
        )

    def optional_field(self, *candidates: str) -> str | None:
        return next((c for c in candidates if c in self.defaults), None)

    def options(self, name: str) -> dict[str, str]:
        """Option key -> display text of a selection field."""
        value = self.defaults.get(self.field(name))
        if not isinstance(value, dict):
            return {}
        return {k: (v.get("value", k) if isinstance(v, dict) else str(v)) for k, v in value.items()}

    def wrap(self, record: dict) -> dict:
        """Wrap the record in the root key and check the field names."""
        unknown = [k for k in record if k not in self.defaults]
        if unknown:
            raise SchemaError(
                f"Unknown fields for {self.path}: {unknown} (available: {', '.join(self.fields)})",
                path=self.path,
            )
        return {self.root_key: record}


class SchemaCache:
    def __init__(self, client: OpnsenseClient):
        self._client = client
        self._cache: dict[str, ModelSchema] = {}
        self._lock = asyncio.Lock()

    async def get(self, path: str) -> ModelSchema:
        if path in self._cache:
            return self._cache[path]
        async with self._lock:
            if path not in self._cache:
                data = await self._client.get(path)
                self._cache[path] = ModelSchema.from_response(path, data)
        return self._cache[path]

    def invalidate(self, path: str | None = None) -> None:
        if path is None:
            self._cache.clear()
        else:
            self._cache.pop(path, None)


def flatten(record: dict) -> dict:
    """Flatten a get_item response: selection fields become a comma-separated
    list of the selected keys."""
    out = {}
    for k, v in record.items():
        if isinstance(v, dict) and all(isinstance(o, dict) and "selected" in o for o in v.values()):
            out[k] = ",".join(key for key, o in v.items() if str(o.get("selected")) in ("1", "True", "true"))
        else:
            out[k] = v
    return out
