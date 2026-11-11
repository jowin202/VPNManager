from __future__ import annotations

from ..client import OpnsenseClient
from ..schema import SchemaCache
from .base import ModelResource, Paths


class FilterRules(ModelResource):
    def __init__(self, client: OpnsenseClient, schemas: SchemaCache):
        super().__init__(client, schemas, Paths(
            "firewall/filter/search_rule", "firewall/filter/get_rule",
            "firewall/filter/add_rule", "firewall/filter/set_rule",
            "firewall/filter/del_rule",
        ))

    async def move_before(self, selected_uuid: str, target_uuid: str) -> None:
        await self.client.post(f"firewall/filter/move_rule_before/{selected_uuid}/{target_uuid}")

    async def apply(self) -> None:
        # Reloads the complete pf ruleset including NAT
        await self.client.post("firewall/filter/apply")
