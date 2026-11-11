"""Host list from several sources and live assignment of hosts to VPNs."""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import asdict, dataclass, field

from ..context import AppContext
from ..opnsense.errors import OpnsenseError
from .inventory import scan
from .naming import Names
from .rows import as_list, pick, uuid_of

log = logging.getLogger(__name__)

SOURCE_RANK = {"discovery": 0, "kea": 1, "dnsmasq": 1, "arp": 2, "alias": 3}


@dataclass
class Host:
    ip: str
    mac: str | None = None
    hostname: str | None = None
    interface: str | None = None
    last_seen: str | None = None
    online: bool = False
    sources: list[str] = field(default_factory=list)
    vpn: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _ipv4(value) -> str | None:
    try:
        ip = ipaddress.ip_address(str(value).split("%")[0])
    except ValueError:
        return None
    return str(ip) if ip.version == 4 else None


def _mac(value) -> str | None:
    if not value:
        return None
    mac = str(value).lower().replace("-", ":")
    return mac if len(mac) == 17 else None


def _merge(hosts: dict[str, Host], source: str, rows: list[dict], online: bool) -> None:
    for row in rows:
        ip = _ipv4(pick(row, "ip_address", "ip", "address", "ipaddr"))
        if not ip:
            continue
        # Devices on the WAN side (upstream router) cannot be assigned
        if any(str(pick(row, k, default="")).lower() == "wan"
               for k in ("interface_name", "intf_description", "if_descr", "if_name")):
            continue
        h = hosts.setdefault(ip, Host(ip=ip))
        if source not in h.sources:
            h.sources.append(source)
        h.mac = h.mac or _mac(pick(row, "ether_address", "ether", "mac", "hwaddr", "hw_address", "mac_address"))
        name = pick(row, "hostname", "host", "name", "client_hostname")
        if name and name not in ("?", "*") and (not h.hostname or source in ("kea", "dnsmasq")):
            h.hostname = str(name).rstrip(".")
        h.interface = h.interface or pick(row, "interface_name", "intf_description", "if_descr", "interface", "intf", "if")
        seen = pick(row, "last_seen", "lastseen", "last_updated", "expire", "ends")
        if seen and source in ("discovery", "arp"):
            h.last_seen = str(seen)
        if online:
            h.online = True


async def vpn_aliases(ctx: AppContext) -> dict[str, dict]:
    """VPN name -> {"alias": name, "uuid": ..., "hosts": [ips]}"""
    result = {}
    for name, objs in (await scan(ctx.ops, ctx.settings.prefix)).items():
        alias_name = Names(ctx.settings.prefix, name).hosts_alias
        row = next((r for r in objs.aliases if pick(r, "name") == alias_name), None)
        if not row or not uuid_of(row):
            continue
        record = await ctx.ops.aliases.get(uuid_of(row))
        result[name] = {"alias": alias_name, "uuid": uuid_of(row), "hosts": as_list(record.get("content"))}
    return result


async def list_hosts(ctx: AppContext) -> list[Host]:
    src = ctx.ops.hosts
    hosts: dict[str, Host] = {}
    arp = await src.arp()
    _merge(hosts, "discovery", await src.discovery(), online=True)
    _merge(hosts, "arp", arp, online=True)
    _merge(hosts, "kea", await src.kea_leases(), online=False)
    _merge(hosts, "dnsmasq", await src.dnsmasq_leases(), online=False)

    # The firewall's own addresses appear as permanent ARP entries
    for row in arp:
        if pick(row, "permanent") is True:
            hosts.pop(str(_ipv4(pick(row, "ip"))), None)

    aliases = await vpn_aliases(ctx)
    pinned = set()
    for vpn, info in aliases.items():
        for ip in info["hosts"]:
            h = hosts.setdefault(ip, Host(ip=ip, sources=["alias"]))
            h.vpn = vpn
            pinned.add(ip)

    # Deduplicate by MAC: assigned IPs always stay, otherwise keep the best source
    by_mac: dict[str, list[Host]] = {}
    for h in hosts.values():
        if h.mac:
            by_mac.setdefault(h.mac, []).append(h)
    for group in by_mac.values():
        if len(group) < 2:
            continue
        keep = min(group, key=lambda h: (not h.online, min(SOURCE_RANK[s] for s in h.sources)))
        for h in group:
            if h is not keep and h.ip not in pinned:
                del hosts[h.ip]

    return sorted(hosts.values(), key=lambda h: ipaddress.IPv4Address(h.ip))


class AssignmentError(ValueError):
    pass


def normalize_changes(changes: list[tuple[str, str | None]], known_vpns: set[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for ip, vpn in changes:
        norm = _ipv4(ip)
        if not norm or norm != ip.strip():
            raise AssignmentError(f"'{ip}' is not a valid IPv4 address.")
        if vpn is not None and vpn not in known_vpns:
            raise AssignmentError(f"VPN '{vpn}' does not exist.")
        if norm in result and result[norm] != vpn:
            raise AssignmentError(f"{norm} is listed several times with different targets.")
        result[norm] = vpn
    return result


async def assign(ctx: AppContext, changes: list[tuple[str, str | None]]) -> dict:
    """Assign hosts exclusively to VPNs (None = regular WAN). Batched: one alias
    reconfigure for all changes. Runs under ctx.writing()."""
    ops = ctx.ops
    aliases = await vpn_aliases(ctx)
    wanted = normalize_changes(changes, set(aliases))

    new_content = {vpn: list(info["hosts"]) for vpn, info in aliases.items()}
    live_remove: list[tuple[str, str]] = []
    live_add: list[tuple[str, str]] = []
    moved: list[str] = []
    for ip, target in wanted.items():
        current = [vpn for vpn, hosts in new_content.items() if ip in hosts]
        if current == ([target] if target else []):
            continue
        moved.append(ip)
        for vpn in current:
            if vpn != target:
                new_content[vpn].remove(ip)
                live_remove.append((aliases[vpn]["alias"], ip))
        if target and target not in current:
            new_content[target].append(ip)
            live_add.append((aliases[target]["alias"], ip))

    if not moved:
        return {"changed": [], "verified": True, "warnings": []}

    # 1. Persistent
    schema = await ops.aliases.schema()
    content_field = schema.field("content")
    for vpn, hosts in new_content.items():
        if hosts != aliases[vpn]["hosts"]:
            await ops.aliases.update(aliases[vpn]["uuid"], {content_field: "\n".join(hosts)})
    await ops.aliases.reconfigure()

    # 2. Live on the pf table (in case reconfigure takes effect with a delay)
    warnings: list[str] = []
    for alias, ip in live_remove:
        await _best_effort(ops.aliases.table_delete(alias, ip), warnings, f"remove {ip} from {alias}")
    for alias, ip in live_add:
        await _best_effort(ops.aliases.table_add(alias, ip), warnings, f"add {ip} to {alias}")

    # 3. Force running connections to be re-established via the right gateway
    for ip in moved:
        await _best_effort(ops.system.kill_states(ip), warnings, f"kill states of {ip}")

    # 4. Verify
    verified = True
    tables = {}
    for vpn, info in aliases.items():
        try:
            tables[vpn] = await ops.aliases.table_list(info["alias"])
        except OpnsenseError as e:
            warnings.append(f"Cannot read table {info['alias']}: {e.message}")
            verified = False
    for ip in moved:
        target = wanted[ip]
        for vpn, table in tables.items():
            if (ip in table) != (vpn == target):
                verified = False
                warnings.append(f"{ip}: pf table of {vpn} does not match (yet).")
    return {"changed": [{"ip": ip, "vpn": wanted[ip]} for ip in moved], "verified": verified, "warnings": warnings}


async def _best_effort(coro, warnings: list[str], what: str) -> None:
    try:
        await coro
    except OpnsenseError as e:
        log.warning("%s failed: %s", what, e.message)
        warnings.append(f"{what} failed: {e.message}")
