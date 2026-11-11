"""Interface assignments (since 26.7, interface settings since 26.7.6).
Records are addressed by interface name (optX)."""

from __future__ import annotations

from ..client import OpnsenseClient
from ..errors import OpnsenseError
from ..schema import SchemaCache
from .base import ModelResource, Paths


class Assignments(ModelResource):
    def __init__(self, client: OpnsenseClient, schemas: SchemaCache):
        super().__init__(client, schemas, Paths(
            "interfaces/assignment/search_item", "interfaces/assignment/get_item",
            "interfaces/assignment/add_item", "interfaces/assignment/set_item",
            "interfaces/assignment/del_item",
        ))

    async def add(self, record: dict) -> str:
        schema = await self.schema()
        res = await self.client.post(self.paths.add, schema.wrap(record))
        for key in ("ifname", "identifier", "uuid"):
            if isinstance(res, dict) and res.get(key):
                return res[key]
        raise OpnsenseError(f"Assignment did not return an interface name: {res}", path=self.paths.add)

    async def delete(self, key: str) -> None:
        """Locked assignments (lock=1) cannot be deleted, so unlock first.
        The device (wgN) must still exist for that."""
        record = await self.get(key)
        if str(record.get("lock")) == "1":
            await self.update(key, {"if": record.get("if", ""), "lock": "0"})
        await super().delete(key)

    async def pending(self) -> object:
        return await self.client.get("interfaces/assignment/pending")

    async def reconfigure(self) -> None:
        await self.client.post("interfaces/assignment/reconfigure")

    async def interface_config(self) -> dict:
        """Runtime data of all interfaces (addresses) for verification."""
        return await self.client.get("diagnostics/interface/get_interface_config")
