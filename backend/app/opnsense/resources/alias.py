from __future__ import annotations

import ipaddress
from typing import Any

from ..client import OpnsenseClient
from ..schema import SchemaCache
from .base import ModelResource, Paths


class Aliases(ModelResource):
    def __init__(self, client: OpnsenseClient, schemas: SchemaCache):
        super().__init__(client, schemas, Paths(
            "firewall/alias/search_item", "firewall/alias/get_item",
            "firewall/alias/add_item", "firewall/alias/set_item",
            "firewall/alias/del_item",
        ))

    async def reconfigure(self) -> None:
        await self.client.post("firewall/alias/reconfigure")

    # Live operations on the pf table (take effect immediately)
    async def table_list(self, alias: str) -> set[str]:
        data = await self.client.get(f"firewall/alias_util/list/{alias}")
        return _collect_ips(data)

    async def table_add(self, alias: str, address: str) -> None:
        await self.client.post(f"firewall/alias_util/add/{alias}", {"address": address})

    async def table_delete(self, alias: str, address: str) -> None:
        await self.client.post(f"firewall/alias_util/delete/{alias}", {"address": address})


def _collect_ips(data: Any) -> set[str]:
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            try:
                found.add(str(ipaddress.ip_address(v.split("/")[0])))
            except ValueError:
                pass

    walk(data)
    return found
