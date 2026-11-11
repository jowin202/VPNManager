from __future__ import annotations

from ..client import OpnsenseClient
from ..schema import SchemaCache
from .base import ModelResource, Paths


class Gateways(ModelResource):
    def __init__(self, client: OpnsenseClient, schemas: SchemaCache):
        super().__init__(client, schemas, Paths(
            "routing/settings/search_gateway", "routing/settings/get_gateway",
            "routing/settings/add_gateway", "routing/settings/set_gateway",
            "routing/settings/del_gateway",
        ))

    async def reconfigure(self) -> None:
        await self.client.post("routing/settings/reconfigure")

    async def status(self) -> list[dict]:
        data = await self.client.get("routes/gateway/status")
        return list(data.get("items", [])) if isinstance(data, dict) else []
