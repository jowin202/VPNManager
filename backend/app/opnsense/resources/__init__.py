from __future__ import annotations

from dataclasses import dataclass

from ..client import OpnsenseClient
from ..schema import SchemaCache
from .alias import Aliases
from .assignment import Assignments
from .filter import FilterRules
from .gateway import Gateways
from .hosts import HostSources
from .source_nat import SourceNatRules
from .system import System
from .wireguard import WireGuard


@dataclass
class Opnsense:
    client: OpnsenseClient
    schemas: SchemaCache
    wireguard: WireGuard
    assignments: Assignments
    gateways: Gateways
    aliases: Aliases
    filter: FilterRules
    source_nat: SourceNatRules
    hosts: HostSources
    system: System

    @classmethod
    def create(cls, client: OpnsenseClient) -> "Opnsense":
        schemas = SchemaCache(client)
        return cls(
            client=client,
            schemas=schemas,
            wireguard=WireGuard(client, schemas),
            assignments=Assignments(client, schemas),
            gateways=Gateways(client, schemas),
            aliases=Aliases(client, schemas),
            filter=FilterRules(client, schemas),
            source_nat=SourceNatRules(client, schemas),
            hosts=HostSources(client),
            system=System(client),
        )
