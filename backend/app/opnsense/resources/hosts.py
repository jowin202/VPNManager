"""Raw data from the host sources. Missing services (404) yield an empty list."""

from __future__ import annotations

from ..client import OpnsenseClient
from ..errors import OpnsenseNotFound


class HostSources:
    def __init__(self, client: OpnsenseClient):
        self.client = client

    async def _rows(self, path: str) -> list[dict]:
        try:
            data = await self.client.get(path, params={"current": 1, "rowCount": -1})
        except OpnsenseNotFound:
            return []
        if isinstance(data, dict):
            return list(data.get("rows", []))
        return list(data) if isinstance(data, list) else []

    async def discovery(self) -> list[dict]:
        return await self._rows("hostdiscovery/service/search")

    async def kea_leases(self) -> list[dict]:
        rows = await self._rows("kea/leases4/search")
        return rows or await self._rows("kea/leases/search")

    async def dnsmasq_leases(self) -> list[dict]:
        return await self._rows("dnsmasq/leases/search")

    async def arp(self) -> list[dict]:
        return await self._rows("diagnostics/interface/search_arp")
