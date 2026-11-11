from __future__ import annotations

import re
from typing import Any

from ..client import OpnsenseClient


class System:
    def __init__(self, client: OpnsenseClient):
        self.client = client

    async def version(self) -> str | None:
        data = await self.client.get("core/firmware/info")
        return find_version(data)

    async def kill_states(self, ip: str) -> None:
        await self.client.post("diagnostics/firewall/kill_states", {"filter": ip})


def find_version(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("product_version", "product_latest"):
            if isinstance(data.get(key), str) and data[key]:
                return data[key]
        for v in data.values():
            found = find_version(v)
            if found:
                return found
    return None


def parse_version(text: str) -> tuple[int, ...]:
    """'26.7.8_2' -> (26, 7, 8, 2)"""
    parts = [int(p) for p in re.findall(r"\d+", text)]
    if len(parts) < 2:
        raise ValueError(f"Unknown version format: {text}")
    return tuple(parts)
