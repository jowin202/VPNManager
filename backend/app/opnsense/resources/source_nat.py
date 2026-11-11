from __future__ import annotations

from ..client import OpnsenseClient
from ..schema import SchemaCache, flatten
from .base import ModelResource, Paths


class SourceNatRules(ModelResource):
    """search_rule only returns manual rules in hybrid/advanced mode; in automatic
    mode OPNsense ignores them entirely. Hence mode handling and a
    mode-independent listing via the filter model."""

    def __init__(self, client: OpnsenseClient, schemas: SchemaCache):
        super().__init__(client, schemas, Paths(
            "firewall/source_nat/search_rule", "firewall/source_nat/get_rule",
            "firewall/source_nat/add_rule", "firewall/source_nat/set_rule",
            "firewall/source_nat/del_rule",
        ))

    async def mode(self) -> str:
        data = await self.client.get("firewall/source_nat/get")
        general = flatten(data["filter"]["general"])
        return general["snat_mode"]

    async def set_mode(self, mode: str) -> None:
        await self.client.post("firewall/source_nat/set", {"filter": {"general": {"snat_mode": mode}}})

    async def manual_rules(self) -> dict[str, dict]:
        """All manual source NAT rules (uuid -> flat record), regardless of mode."""
        data = await self.client.get("firewall/filter/get")
        rules = data["filter"]["snatrules"].get("rule") or {}
        return {uuid: flatten(rec) for uuid, rec in rules.items()} if isinstance(rules, dict) else {}
