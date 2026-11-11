"""Determines the actual state of all objects created by the VPN Manager
directly from OPNsense (the source of truth), using name/description markers."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..opnsense.resources import Opnsense
from .naming import interface_owner, owner_of
from .rows import pick, uuid_of


@dataclass
class VpnObjects:
    name: str
    peers: list[dict] = field(default_factory=list)
    servers: list[dict] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    gateways: list[dict] = field(default_factory=list)
    aliases: list[dict] = field(default_factory=list)
    nat_rules: list[str] = field(default_factory=list)
    filter_rules: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any((self.peers, self.servers, self.interfaces, self.gateways,
                        self.aliases, self.nat_rules, self.filter_rules))

    def summary(self) -> dict:
        return {
            "peers": len(self.peers),
            "instances": len(self.servers),
            "interfaces": list(self.interfaces),
            "gateways": [g.get("name") for g in self.gateways],
            "aliases": [a.get("name") for a in self.aliases],
            "source_nat_rules": len(self.nat_rules),
            "filter_rules": len(self.filter_rules),
        }

    @property
    def complete(self) -> bool:
        return bool(self.peers and self.servers and self.interfaces and self.gateways
                    and self.aliases and self.filter_rules)


def device_of(server_row: dict) -> str | None:
    instance = pick(server_row, "instance")
    if instance not in (None, ""):
        return f"wg{instance}"
    dev = pick(server_row, "interface", "device")
    return dev if isinstance(dev, str) and dev.startswith("wg") else None


def assignment_device(row: dict) -> str | None:
    return pick(row, "device", "if", "interface")


def assignment_ifname(row: dict) -> str | None:
    return pick(row, "identifier", "ifname", "uuid")


def assignment_descr(row: dict) -> str | None:
    return pick(row, "descr", "description")


async def scan(ops: Opnsense, prefix: str) -> dict[str, VpnObjects]:
    result: dict[str, VpnObjects] = {}

    def obj(name: str) -> VpnObjects:
        return result.setdefault(name, VpnObjects(name))

    for row in await ops.wireguard.clients.search():
        name = owner_of(pick(row, "name"), prefix)
        if name:
            obj(name).peers.append(row)

    for row in await ops.wireguard.servers.search():
        name = owner_of(pick(row, "name"), prefix)
        if name:
            o = obj(name)
            o.servers.append(row)
            dev = device_of(row)
            if dev:
                o.devices.append(dev)

    device_owner = {d: o.name for o in result.values() for d in o.devices}
    for row in await ops.assignments.search():
        dev = assignment_device(row)
        ifname = assignment_ifname(row)
        name = device_owner.get(dev) if dev else None
        if not name:
            # Instance already deleted (half-removed VPN): match by description
            upper = interface_owner(assignment_descr(row), prefix)
            if upper:
                name = next((n for n in result if n.upper() == upper), upper.lower())
        if name and ifname:
            obj(name).interfaces.append(ifname)

    for row in await ops.gateways.search():
        name = owner_of(pick(row, "descr", "description"), prefix)
        if name:
            obj(name).gateways.append(row)

    for row in await ops.aliases.search():
        name = owner_of(pick(row, "description", "descr"), prefix)
        if name:
            obj(name).aliases.append(row)

    for uuid, rec in (await ops.source_nat.manual_rules()).items():
        name = owner_of(pick(rec, "description"), prefix)
        if name:
            obj(name).nat_rules.append(uuid)

    for row in await ops.filter.search():
        name = owner_of(pick(row, "description", "descr"), prefix)
        if name and uuid_of(row):
            obj(name).filter_rules.append(uuid_of(row))

    return result
