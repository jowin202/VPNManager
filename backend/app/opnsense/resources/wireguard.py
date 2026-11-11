from __future__ import annotations

from ..client import OpnsenseClient
from ..schema import SchemaCache
from .base import ModelResource, Paths


class WireGuard:
    def __init__(self, client: OpnsenseClient, schemas: SchemaCache):
        self.client = client
        self.schemas = schemas
        self.servers = ModelResource(client, schemas, Paths(
            "wireguard/server/search_server", "wireguard/server/get_server", "wireguard/server/add_server",
            "wireguard/server/set_server", "wireguard/server/del_server",
        ))
        self.clients = ModelResource(client, schemas, Paths(
            "wireguard/client/search_client", "wireguard/client/get_client", "wireguard/client/add_client",
            "wireguard/client/set_client", "wireguard/client/del_client",
        ))

    async def general(self) -> dict:
        return await self.client.get("wireguard/general/get")

    async def set_enabled(self) -> None:
        schema = await self.schemas.get("wireguard/general/get")
        await self.client.post("wireguard/general/set", schema.wrap({schema.field("enabled"): "1"}))

    async def reconfigure(self) -> None:
        await self.client.post("wireguard/service/reconfigure")

    async def show(self) -> dict:
        return await self.client.get("wireguard/service/show")
