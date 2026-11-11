"""Create/delete VPN workflows with compensations (rollback)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from dataclasses import dataclass, field, replace

from ..context import AppContext
from ..opnsense.errors import OpnsenseError, OpnsenseNotFound, SchemaError
from .inventory import (
    VpnObjects,
    assignment_device,
    assignment_ifname,
    device_of,
    scan,
)
from .naming import Names, owner_of, validate_name
from .parser import WgConfig, parse_wg_quick
from .rows import as_list, pick, uuid_of
from .status import HANDSHAKE_MAX_AGE, gateway_state, handshake_age
from .steps import Job, Saga, Step

log = logging.getLogger(__name__)

CREATE_STEPS = [
    ("peer", "Create WireGuard peer"),
    ("instance", "Create WireGuard instance"),
    ("service", "Enable and apply WireGuard"),
    ("assignment", "Assign and enable interface"),
    ("gateway", "Create gateway"),
    ("aliases", "Create aliases"),
    ("source_nat", "Source NAT"),
    ("rules", "Firewall rules (policy routing, kill switch)"),
    ("verify", "Check tunnel and gateway"),
]

DELETE_STEPS = [
    ("rules", "Delete firewall rules"),
    ("source_nat", "Delete source NAT rules"),
    ("aliases", "Delete aliases"),
    ("gateway", "Delete gateway"),
    ("assignment", "Delete interface assignment"),
    ("instance", "Delete WireGuard instance"),
    ("peer", "Delete WireGuard peer"),
    ("states", "Reset states of the hosts"),
]


class Conflict(Exception):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ProvisioningError(Exception):
    pass


@dataclass
class CreateRequest:
    name: str
    config: str
    gateway_ip: str | None = None
    allow_pending_changes: bool = False
    # Custom tunnel address instead of the one from the config (e.g. "10.5.0.2/32")
    tunnel_address: str | None = None


@dataclass
class Plan:
    names: Names
    cfg: WgConfig
    gateway_ip: str
    monitor_ip: str = ""
    # Original IPv4 tunnel address if it was replaced
    rewritten_from: str | None = None
    # IPv6 tunnel addresses that were dropped because they are already in use
    dropped_ipv6: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Preparation (read-only)
# ---------------------------------------------------------------------------

def choose_gateway_ip(cfg: WgConfig, taken: set[str] = frozenset(), *, use_dns: bool = True) -> str:
    """Gateway IP for the static gateway. With "far gateway" on a point-to-point
    interface it only serves as a next-hop marker:
    1. the provider's DNS if it is private IPv4 (Proton 10.2.0.1, Mullvad 10.64.0.1),
    2. otherwise the first host address of the tunnel network (the enclosing /24 for a /32),
    each different from our own address and not yet used by another gateway."""
    own = cfg.ipv4_addresses[0]
    candidates: list[ipaddress.IPv4Address] = [
        d for d in cfg.dns if use_dns and d.version == 4 and d.is_private
    ]
    net = own.network if own.network.prefixlen <= 30 else ipaddress.ip_network(f"{own.ip}/24", strict=False)
    candidates += list(net.hosts())[:16]
    for c in candidates:
        if c != own.ip and str(c) not in taken:
            return str(c)
    raise Conflict("No free gateway IP in the tunnel network; please specify gateway_ip.")


def prepare(prefix: str, req: CreateRequest) -> tuple[Names, WgConfig]:
    names = Names(prefix, validate_name(req.name))
    cfg = parse_wg_quick(req.config)
    if req.gateway_ip:
        ipaddress.IPv4Address(req.gateway_ip)
    if req.tunnel_address:
        ipaddress.IPv4Interface(req.tunnel_address)
    return names, cfg


async def used_addresses(ctx: AppContext) -> tuple[set[str], list[ipaddress.IPv4Interface]]:
    """Tunnel addresses of all WireGuard instances and static interface addresses."""
    tunnel: set[str] = set()
    for row in await ctx.ops.wireguard.servers.search():
        tunnel |= {a.split("/")[0] for a in as_list(pick(row, "tunneladdress", "address"))}
    interfaces: list[ipaddress.IPv4Interface] = []
    for row in await ctx.ops.assignments.search():
        ip, bits = pick(row, "ipaddr"), pick(row, "subnet", default="24")
        try:
            interfaces.append(ipaddress.IPv4Interface(f"{ip}/{bits}"))
        except ValueError:
            continue
    return tunnel, interfaces


def resolve_tunnel_address(cfg: WgConfig, used: set[str], interfaces: list[ipaddress.IPv4Interface],
                           override: str | None = None) -> tuple[WgConfig, str | None]:
    """Some providers (Proton) put the same tunnel address into every config
    (10.2.0.2). Two wg interfaces with the same address collide in routing.
    Proton translates the address server-side anyway, so a different one works:
    use the next free 10.x.0.2/32 (x from 3) or the address given explicitly.
    Duplicate IPv6 addresses (Proton: 2a07:b944::2:2) are dropped – the VPN
    Manager only routes IPv4, the IPv6 tunnel address is not needed."""
    own4 = cfg.ipv4_addresses[0]
    if any(str(a.ip) in used for a in cfg.ipv6_addresses):
        cfg = replace(cfg, addresses=[a for a in cfg.addresses if a.version == 4 or str(a.ip) not in used])

    def free(candidate: ipaddress.IPv4Interface) -> bool:
        net24 = ipaddress.ip_network(f"{candidate.ip}/24", strict=False)
        return (not any(ipaddress.ip_address(u) in net24 for u in used if ":" not in u)
                and not any(i.network.overlaps(net24) for i in interfaces))

    if override:
        new = ipaddress.IPv4Interface(override)
        if str(new.ip) in used:
            raise Conflict(f"Tunnel address {new.ip} is already in use.")
    elif str(own4.ip) not in used:
        return cfg, None
    else:
        new = next((c for x in range(3, 255)
                    if free(c := ipaddress.IPv4Interface(f"10.{x}.0.2/32"))), None)
        if new is None:
            raise Conflict("No free tunnel address in the range 10.3–10.254.0.2; please specify one.")
    addresses = [new if a == own4 else a for a in cfg.addresses]
    return replace(cfg, addresses=addresses), str(own4.ip)


def _pending(status: object, rows: list[dict]) -> list[str]:
    """Interfaces with unapplied changes. Since 26.7.6 every assignment carries
    `pending_action`; without pending changes `assignments/pending` only returns
    {"status": "ok"} – any other response is treated as pending to be safe."""
    pending = [str(assignment_ifname(r)) for r in rows if pick(r, "pending_action")]
    if not pending and not (isinstance(status, dict) and set(status) <= {"status"}
                            and str(status.get("status", "ok")).lower() == "ok"):
        pending.append("unknown")
    return pending


async def check_pending(ctx: AppContext, allow: bool) -> None:
    if allow:
        return
    status = await ctx.ops.assignments.pending()
    pending = _pending(status, await ctx.ops.assignments.search())
    if pending:
        raise Conflict(
            "OPNsense has unapplied interface changes "
            f"({', '.join(pending)}). Applying would include them. Please apply or discard them in the "
            "OPNsense GUI first, or explicitly continue with 'allow_pending_changes'.",
            {"pending": pending, "status": status},
        )


async def precheck(ctx: AppContext, names: Names, cfg: WgConfig, req: CreateRequest) -> Plan:
    inventory = await scan(ctx.ops, ctx.settings.prefix)
    existing = inventory.get(names.name)
    if existing and not existing.empty:
        raise Conflict(
            f"Objects for '{names.name}' already exist in OPNsense (possibly a half-created VPN). "
            "Please delete it first and then create it again.",
            {"objects": existing.summary()},
        )
    clash = next((n for n in inventory if n.upper() == names.upper), None)
    if clash:
        raise Conflict(f"Name collides with existing VPN '{clash}' (case-insensitive).")

    used, interfaces = await used_addresses(ctx)
    original = cfg
    cfg, rewritten_from = resolve_tunnel_address(cfg, used, interfaces, req.tunnel_address)
    dropped_ipv6 = [str(a) for a in original.ipv6_addresses if a not in cfg.addresses]

    gateways = await ctx.ops.gateways.search()
    taken = {str(pick(r, "gateway")) for r in gateways if pick(r, "gateway")}
    monitors = {str(pick(r, "monitor")) for r in [*gateways, *await ctx.ops.gateways.status()]}
    monitor_ip = next((ip for ip in ctx.settings.monitor_ips if ip not in monitors), None)
    if monitor_ip is None:
        raise Conflict(
            "All monitor IPs are already used by other gateways (OPNsense allows each only once). "
            "Please add more to VPNMGR_MONITOR_IPS."
        )
    if req.gateway_ip:
        if req.gateway_ip in taken:
            raise Conflict(f"Gateway IP {req.gateway_ip} is already in use.")
        gateway_ip = req.gateway_ip
    else:
        # After rewriting, the provider DNS (10.2.0.1) no longer matches the tunnel network
        gateway_ip = choose_gateway_ip(cfg, taken, use_dns=rewritten_from is None)

    await check_pending(ctx, req.allow_pending_changes)
    return Plan(names=names, cfg=cfg, gateway_ip=gateway_ip, monitor_ip=monitor_ip,
                rewritten_from=rewritten_from, dropped_ipv6=dropped_ipv6)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def new_create_job(name: str) -> Job:
    return Job(kind="create", name=name, steps=[Step(k, t) for k, t in CREATE_STEPS])


async def create_vpn(ctx: AppContext, job: Job, plan: Plan, *, allow_pending_changes: bool = False,
                     verify_timeout: float = 30.0, poll_interval: float = 3.0,
                     gateway_option_timeout: float = 90.0) -> None:
    """Runs under ctx.writing(); fills job with progress, errors and rollback."""
    ops, s = ctx.ops, ctx.settings
    names, cfg = plan.names, plan.cfg
    saga = Saga(job)
    state: dict = {}

    async def peer(step: Step) -> None:
        schema = await ops.wireguard.clients.schema()
        record = {
            schema.field("enabled"): "1",
            schema.field("name"): names.wg_name,
            schema.field("pubkey"): cfg.peer.public_key,
            schema.field("serveraddress"): cfg.peer.endpoint_host,
            schema.field("serverport"): str(cfg.peer.endpoint_port),
            schema.field("tunneladdress"): ",".join(str(n) for n in cfg.peer.allowed_ips),
            schema.field("keepalive"): str(cfg.peer.keepalive),
        }
        if cfg.peer.preshared_key:
            record[schema.field("psk")] = cfg.peer.preshared_key
        uuid = await ops.wireguard.clients.add(record)
        state["peer"] = uuid
        saga.on_rollback("Delete WireGuard peer", lambda: ops.wireguard.clients.delete(uuid))

    async def instance(step: Step) -> None:
        schema = await ops.wireguard.servers.schema()
        record = {
            schema.field("enabled"): "1",
            schema.field("name"): names.wg_name,
            schema.field("privkey"): cfg.private_key,
            schema.field("pubkey"): cfg.public_key,
            schema.field("tunneladdress"): ",".join(str(a) for a in cfg.addresses),
            # Otherwise AllowedIPs=0.0.0.0/0 would set the default route for the whole network
            schema.field("disableroutes"): "1",
        }
        if cfg.mtu:
            record[schema.field("mtu")] = str(cfg.mtu)
        peers_field = schema.optional_field("peers")
        if peers_field:
            record[peers_field] = state["peer"]
        uuid = await ops.wireguard.servers.add(record)
        state["server"] = uuid
        notes = []
        if plan.rewritten_from:
            notes.append(f"Tunnel address {plan.rewritten_from} is already in use, using {cfg.ipv4_addresses[0]}.")
        if plan.dropped_ipv6:
            notes.append(f"IPv6 address {', '.join(plan.dropped_ipv6)} is already in use and is dropped "
                         "(only IPv4 is routed).")
        if notes:
            step.detail = " ".join(notes)

        async def undo() -> None:
            await ops.wireguard.servers.delete(uuid)
            await ops.wireguard.reconfigure()

        saga.on_rollback("Delete WireGuard instance", undo)
        if not peers_field:
            client_schema = await ops.wireguard.clients.schema()
            await ops.wireguard.clients.update(state["peer"], {client_schema.field("servers"): uuid})

    async def service(step: Step) -> None:
        general = await ops.wireguard.general()
        root = next(iter(general.values()), {}) if isinstance(general, dict) else {}
        if str(root.get("enabled", "0")) != "1":
            await ops.wireguard.set_enabled()
            step.detail = "WireGuard service enabled."
        await ops.wireguard.reconfigure()
        server = await ops.wireguard.servers.get(state["server"])
        device = device_of(server)
        if not device:
            raise ProvisioningError("Could not determine the device (wgN) of the new instance.")
        state["device"] = device
        step.detail = f"{step.detail + ' ' if step.detail else ''}Device {device}."

    async def assignment(step: Step) -> None:
        await check_pending(ctx, allow_pending_changes)
        ops.schemas.invalidate(ops.assignments.paths.get)  # device options have changed
        schema = await ops.assignments.schema()
        dev_field = schema.field("if")
        options = schema.options(dev_field)
        if options and state["device"] not in options:
            raise ProvisioningError(f"Device {state['device']} is not offered for assignment.")
        record = {
            dev_field: state["device"],
            schema.field("descr"): names.interface_descr,
            schema.field("enable"): "1",
            # Default is lock=1; locked assignments cannot be deleted
            schema.field("lock"): "0",
        }
        # MSS clamping: hosts behind the firewall assume MTU 1500, the tunnel has
        # less (WireGuard default 1420). Without clamping, large TCP packets must be
        # fragmented or PMTUD fails – which costs a lot of throughput.
        # OPNsense clamps to the configured value minus 40 (IPv4).
        mss_field = schema.optional_field("mss")
        if mss_field:
            record[mss_field] = str(cfg.mtu or WG_DEFAULT_MTU)
        # The address comes from WireGuard: IP type "None" (option value "" in 26.7.8)
        for f in ("type4", "type6"):
            opts = schema.options(f)
            none = next((k for k in ("", "none") if k in opts), None)
            if none is None:
                raise SchemaError(f"Field {f} offers no type 'None': {list(opts)}", path=schema.path)
            record[f] = none
        ifname = await ops.assignments.add(record)
        state["ifname"] = ifname

        async def undo() -> None:
            await ops.assignments.delete(ifname)
            await ops.assignments.reconfigure()

        saga.on_rollback(f"Delete interface assignment {ifname}", undo)
        await ops.assignments.reconfigure()

        # opnsense/core#10996: the apply must not remove the tunnel address
        ip = str(cfg.ipv4_addresses[0].ip)
        ok = await _device_has_address(ctx, state["device"], ip)
        if ok is False:
            await ops.wireguard.reconfigure()
            await asyncio.sleep(1)
            ok = await _device_has_address(ctx, state["device"], ip)
            if ok is False:
                raise ProvisioningError(
                    f"{state['device']} does not have the tunnel address {ip} after the apply "
                    "(see opnsense/core#10996)."
                )
        if ok is None:
            step.status = "warning"
            step.detail = f"{ifname}; could not verify the tunnel address."
            job.warnings.append(step.detail)
        else:
            step.detail = f"{state['device']} → {ifname}, address {ip} active."

    async def gateway(step: Step) -> None:
        schema = await ops.gateways.schema()
        record = {
            schema.field("name"): names.gateway,
            schema.field("descr", "description"): names.tag,
            schema.field("interface"): state["ifname"],
            schema.field("ipprotocol"): "inet",
            schema.field("gateway"): plan.gateway_ip,
            schema.field("fargw"): "1",
            schema.field("defaultgw"): "0",
        }
        optional = {
            ("disabled",): "0",
            ("monitor_disable",): "0",
            ("monitor",): plan.monitor_ip,
            ("priority",): "255",
            ("weight",): "1",
            # Overseas VPN servers have 200–300 ms; OPNsense would warn from 200 ms
            ("latencylow",): GW_LATENCY_LOW,
            ("latencyhigh",): GW_LATENCY_HIGH,
        }
        for cands, value in optional.items():
            f = schema.optional_field(*cands)
            if f:
                record[f] = value
        uuid = await ops.gateways.add(record)

        async def undo() -> None:
            await ops.gateways.delete(uuid)
            await ops.gateways.reconfigure()

        saga.on_rollback(f"Delete gateway {names.gateway}", undo)
        await ops.gateways.reconfigure()
        step.detail = f"{names.gateway} via {plan.gateway_ip}, Monitor {plan.monitor_ip}."

    async def aliases(step: Step) -> None:
        schema = await ops.aliases.schema()
        base = {
            schema.field("enabled"): "1",
            schema.field("description"): names.tag,
        }
        wanted = [(names.hosts_alias, "host", "")]
        if not s.local_networks_alias:
            wanted.append((names.local_alias, "network", "\n".join(s.local_networks)))
        for alias_name, typ, content in wanted:
            uuid = await ops.aliases.add({
                **base,
                schema.field("name"): alias_name,
                schema.field("type"): typ,
                schema.field("content"): content,
            })

            async def undo(uuid: str = uuid) -> None:
                await ops.aliases.delete(uuid)
                await ops.aliases.reconfigure()

            saga.on_rollback(f"Delete alias {alias_name}", undo)
        await ops.aliases.reconfigure()
        state["local_alias"] = s.local_networks_alias or names.local_alias
        step.detail = ", ".join(a for a, _, _ in wanted)

    async def source_nat(step: Step) -> None:
        if s.source_nat == "never":
            step.status = "skipped"
            step.detail = "Disabled (VPNMGR_SOURCE_NAT=never)."
            return
        if s.source_nat == "auto":
            for row in await ops.source_nat.search():
                if pick(row, "interface") == state["ifname"] and not owner_of(pick(row, "description"), s.prefix):
                    step.status = "skipped"
                    step.detail = f"Existing rule for {state['ifname']} found, no own rule needed."
                    return
        notes = await ensure_hybrid_nat(ctx, saga)
        schema = await ops.source_nat.schema()
        target_field = schema.field("target")
        target = f"{state['ifname']}ip"
        opts = schema.options(target_field)
        if opts and target not in opts:
            raise SchemaError(
                f"Translation target '{target}' (interface address) is not among the options: {list(opts)[:20]}",
                path=schema.path,
            )
        record = {
            schema.field("enabled"): "1",
            schema.field("interface"): state["ifname"],
            schema.field("ipprotocol"): "inet",
            schema.field("source_net"): names.hosts_alias,
            schema.field("destination_net"): "any",
            target_field: target,
            schema.field("description"): names.rule_descr("nat"),
        }
        uuid = await ops.source_nat.add(record)

        async def undo() -> None:
            await ops.source_nat.delete(uuid)
            await ops.filter.apply()

        saga.on_rollback("Delete source NAT rule", undo)
        await ops.filter.apply()
        step.detail = f"{names.hosts_alias} → address of {state['ifname']}." + (f" {notes}" if notes else "")

    async def rules(step: Step) -> None:
        # The gateway options for rules come from a configd cache (~20 s TTL);
        # until then OPNsense rejects the new gateway in a rule.
        deadline = time.monotonic() + gateway_option_timeout
        waited = 0.0
        while True:
            ops.schemas.invalidate(ops.filter.paths.get)
            schema = await ops.filter.schema()
            if names.gateway in schema.options("gateway"):
                break
            if time.monotonic() >= deadline:
                raise ProvisioningError(
                    f"Gateway {names.gateway} did not appear in the gateway options for firewall rules "
                    f"after {gateway_option_timeout:.0f}s."
                )
            await asyncio.sleep(poll_interval)
            waited += poll_interval
        interfaces = await resolve_rule_interfaces(ctx)
        if_opts = schema.options("interface")
        unknown = [i for i in interfaces if if_opts and i not in if_opts]
        if unknown:
            raise ProvisioningError(f"Unknown rule interfaces: {unknown} (available: {list(if_opts)})")

        def rule(iface: str, role: str, action: str, destination: str, gateway: str | None) -> dict:
            r = {
                schema.field("enabled"): "1",
                schema.field("action"): action,
                schema.field("quick"): "1",
                schema.field("interface"): iface,
                schema.field("direction"): "in",
                schema.field("ipprotocol"): "inet",
                schema.field("protocol"): "any",
                schema.field("source_net"): names.hosts_alias,
                schema.field("destination_net"): destination,
                schema.field("description"): names.rule_descr(f"{role} {iface}"),
            }
            if gateway:
                r[schema.field("gateway")] = gateway
            return r

        # Registered before the deletions, so it runs after them on rollback
        saga.on_rollback("Apply firewall rules", ops.filter.apply)
        created: list[str] = []
        for iface in interfaces:
            for record in (
                rule(iface, "local", "pass", state["local_alias"], None),
                rule(iface, "vpn", "pass", "any", names.gateway),
                rule(iface, "killswitch", "block", "any", None),
            ):
                uuid = await ops.filter.add(record)
                created.append(uuid)

                async def undo(uuid: str = uuid) -> None:
                    await ops.filter.delete(uuid)

                saga.on_rollback("Delete firewall rule", undo)
        moved = await move_rules_to_top(ctx, created)
        await ops.filter.apply()
        step.detail = (f"{len(created)} rules on {', '.join(interfaces)}"
                       + (", moved to the top" if moved else "")
                       + (f", waited {waited:.0f}s for the gateway options." if waited else "."))

    async def verify(step: Step) -> None:
        deadline = time.monotonic() + verify_timeout
        while True:
            age = handshake_age(await ops.wireguard.show(), cfg.peer.public_key)
            gw = gateway_state(await ops.gateways.status(), names.gateway)
            hs_ok = age is not None and age <= HANDSHAKE_MAX_AGE
            if (hs_ok and gw["state"] == "online") or time.monotonic() >= deadline:
                break
            await asyncio.sleep(poll_interval)
        job.result = {"handshake_age": age, "gateway": gw}
        problems = []
        if not hs_ok:
            problems.append("No recent handshake – the provider is slow or the config/endpoint is wrong.")
        if gw["state"] != "online":
            problems.append(f"Gateway {names.gateway} is {gw['state']}.")
            if hs_ok:
                hint = ("The tunnel is up (handshake ok). Gateway monitoring averages loss over about a "
                        "minute, so \"offline\" right after creation is normal.")
                if plan.rewritten_from:
                    hint += (" If it stays offline: some Proton servers answer pings to "
                             f"{plan.rewritten_from} when the tunnel address was changed; regular traffic "
                             "still works.")
                problems.append(hint)
        if problems:
            step.status = "warning"
            step.detail = " ".join(problems)
            job.warnings.extend(problems)
        else:
            step.detail = f"Handshake {age}s ago, gateway online."

    try:
        for key, fn in (("peer", peer), ("instance", instance), ("service", service),
                        ("assignment", assignment), ("gateway", gateway), ("aliases", aliases),
                        ("source_nat", source_nat), ("rules", rules)):
            await saga.run(key, fn)
        ctx.metadata.put(names.name, {
            "created_at": int(time.time()),
            "dns": [str(d) for d in cfg.dns],
            "gateway_ip": plan.gateway_ip,
            "endpoint": f"{cfg.peer.endpoint_host}:{cfg.peer.endpoint_port}",
            "addresses": [str(a) for a in cfg.addresses],
            "original_address": plan.rewritten_from,
            "monitor_ip": plan.monitor_ip,
            "peer_public_key": cfg.peer.public_key,
        })
        # A missing handshake is not a reason to roll back
        await saga.run("verify", verify)
        job.status = "succeeded"
    except Exception as e:  # noqa: BLE001
        failed = next((st for st in job.steps if st.status == "running"), None)
        if failed:
            failed.status = "failed"
            failed.detail = _message(e)
        job.error = _error_dict(e)
        log.error("Creating %s failed: %s", names.name, _message(e))
        await saga.rollback()
        job.status = "failed"
    finally:
        job.finished_at = time.time()


NAT_FLAG = "__source_nat_mode_changed__"
WG_DEFAULT_MTU = 1420
GW_LATENCY_LOW = "400"
GW_LATENCY_HIGH = "800"


async def ensure_hybrid_nat(ctx: AppContext, saga: Saga) -> str | None:
    """In automatic mode OPNsense does not apply manual source NAT rules.
    Without foreign manual rules, hybrid equals automatic plus our rules; with
    foreign rules those would suddenly become active – so abort in that case."""
    ops = ctx.ops
    mode = await ops.source_nat.mode()
    if mode in ("hybrid", "advanced"):
        return None
    if mode != "automatic":
        raise ProvisioningError(f"Source NAT is disabled in OPNsense (mode '{mode}').")
    foreign = [u for u, r in (await ops.source_nat.manual_rules()).items()
               if not owner_of(pick(r, "description"), ctx.settings.prefix)]
    if foreign:
        raise ProvisioningError(
            f"Source NAT is set to 'automatic', but there are {len(foreign)} foreign manual rule(s) "
            "that would become active when switching to 'hybrid'. Please switch the mode to 'hybrid' "
            "yourself in OPNsense (Firewall → NAT → Source NAT)."
        )
    await ops.source_nat.set_mode("hybrid")
    ctx.metadata.put(NAT_FLAG, {"previous": "automatic"})

    async def undo() -> None:
        await ops.source_nat.set_mode("automatic")
        ctx.metadata.delete(NAT_FLAG)
        await ops.filter.apply()

    saga.on_rollback("Switch source NAT mode back to automatic", undo)
    return "Switched source NAT mode from automatic to hybrid."


async def restore_nat_mode(ctx: AppContext) -> str | None:
    """After deletion: if the VPN Manager switched the mode and no manual rules
    are left, switch back to automatic."""
    if not ctx.metadata.get(NAT_FLAG):
        return None
    if await ctx.ops.source_nat.manual_rules():
        return None
    if await ctx.ops.source_nat.mode() == "hybrid":
        await ctx.ops.source_nat.set_mode("automatic")
        await ctx.ops.filter.apply()
    ctx.metadata.delete(NAT_FLAG)
    return "Source NAT mode switched back to automatic."


async def _device_has_address(ctx: AppContext, device: str, ip: str) -> bool | None:
    try:
        data = await ctx.ops.assignments.interface_config()
    except OpnsenseNotFound:
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(device)
    if entry is None:
        return False
    return ip in json.dumps(entry)


async def resolve_rule_interfaces(ctx: AppContext) -> list[str]:
    """Configured interfaces, otherwise all interfaces where hosts are seen."""
    if ctx.settings.rule_interfaces:
        return list(ctx.settings.rule_interfaces)
    rows = await ctx.ops.assignments.search()
    # Host discovery returns the interface description ("LAN"), ARP the device ("le1")
    lookup: dict[str, str] = {}
    for r in rows:
        ifname = assignment_ifname(r)
        if not ifname:
            continue
        lookup[ifname.lower()] = ifname
        for key in (assignment_device(r), pick(r, "%descr", "descr")):
            if key:
                lookup[str(key).lower()] = ifname
    found: set[str] = set()
    for row in [*await ctx.ops.hosts.discovery(), *await ctx.ops.hosts.arp()]:
        intf = str(pick(row, "interface_name", "intf", "interface", "if", default=""))
        ifname = lookup.get(intf.lower())
        if ifname and ifname != "wan" and not intf.startswith("wg"):
            found.add(ifname)
    return sorted(found) or ["lan"]


async def move_rules_to_top(ctx: AppContext, uuids: list[str]) -> bool:
    """Move our rules (in order) before the first foreign, movable rule.
    Automatic/legacy rules (is_automatic, legacy) cannot be moved."""
    rows = [r for r in await ctx.ops.filter.search()
            if not _truthy(pick(r, "is_automatic")) and not _truthy(pick(r, "legacy"))]
    if all(pick(r, "sort_order") is not None for r in rows):
        rows.sort(key=lambda r: str(pick(r, "sort_order")))
    order = [uuid_of(r) for r in rows]
    target = next((uuid_of(r) for r in rows if not owner_of(pick(r, "description"), ctx.settings.prefix)), None)
    if not target:
        return False
    if all(u in order and order.index(u) < order.index(target) for u in uuids):
        return False
    for uuid in uuids:
        await ctx.ops.filter.move_before(uuid, target)
    return True


def _truthy(value) -> bool:
    return value is True or str(value).lower() in ("1", "true")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class HostsAssigned(Exception):
    def __init__(self, hosts: list[str]):
        super().__init__("Hosts are still assigned to this VPN.")
        self.hosts = hosts


async def hosts_of(ctx: AppContext, objs: VpnObjects) -> list[str]:
    names = Names(ctx.settings.prefix, objs.name)
    for row in objs.aliases:
        if pick(row, "name") == names.hosts_alias and uuid_of(row):
            record = await ctx.ops.aliases.get(uuid_of(row))
            return as_list(record.get("content"))
    return []


def new_delete_job(name: str) -> Job:
    return Job(kind="delete", name=name, steps=[Step(k, t) for k, t in DELETE_STEPS])


async def delete_vpn(ctx: AppContext, name: str, *, confirm_hosts: bool = False,
                     allow_pending_changes: bool = False) -> Job:
    """Deletes all objects of the VPN in reverse order. Idempotent: stops at the
    first error and can simply be called again."""
    ops = ctx.ops
    objs = (await scan(ops, ctx.settings.prefix)).get(name)
    if objs is None or objs.empty:
        raise LookupError(f"VPN '{name}' not found.")
    hosts = await hosts_of(ctx, objs)
    if hosts and not confirm_hosts:
        raise HostsAssigned(hosts)
    if objs.interfaces:
        await check_pending(ctx, allow_pending_changes)

    job = new_delete_job(name)
    saga = Saga(job)

    async def rules(step: Step) -> None:
        for uuid in objs.filter_rules:
            await ops.filter.delete(uuid)
        if objs.filter_rules:
            await ops.filter.apply()
        step.detail = f"{len(objs.filter_rules)} deleted."

    async def source_nat(step: Step) -> None:
        for uuid in objs.nat_rules:
            await ops.source_nat.delete(uuid)
        if objs.nat_rules:
            await ops.filter.apply()
        restored = await restore_nat_mode(ctx)
        step.detail = f"{len(objs.nat_rules)} deleted." + (f" {restored}" if restored else "")

    async def aliases(step: Step) -> None:
        for row in objs.aliases:
            await ops.aliases.delete(uuid_of(row))
        if objs.aliases:
            await ops.aliases.reconfigure()
        step.detail = ", ".join(str(pick(r, "name")) for r in objs.aliases) or "none"

    async def gateway(step: Step) -> None:
        for row in objs.gateways:
            await ops.gateways.delete(uuid_of(row))
        if objs.gateways:
            await ops.gateways.reconfigure()
        step.detail = ", ".join(str(pick(r, "name")) for r in objs.gateways) or "none"

    async def assignment(step: Step) -> None:
        for ifname in objs.interfaces:
            await ops.assignments.delete(ifname)
        if objs.interfaces:
            await ops.assignments.reconfigure()
        step.detail = ", ".join(objs.interfaces) or "none"

    async def instance(step: Step) -> None:
        for row in objs.servers:
            await ops.wireguard.servers.delete(uuid_of(row))
        step.detail = f"{len(objs.servers)} deleted."

    async def peer(step: Step) -> None:
        for row in objs.peers:
            await ops.wireguard.clients.delete(uuid_of(row))
        if objs.servers or objs.peers:
            await ops.wireguard.reconfigure()
        step.detail = f"{len(objs.peers)} deleted."

    async def states(step: Step) -> None:
        for ip in hosts:
            await ops.system.kill_states(ip)
        step.detail = f"{len(hosts)} hosts." if hosts else "No hosts."

    try:
        for key, fn in (("rules", rules), ("source_nat", source_nat), ("aliases", aliases),
                        ("gateway", gateway), ("assignment", assignment), ("instance", instance),
                        ("peer", peer), ("states", states)):
            await saga.run(key, fn)
        ctx.metadata.delete(name)
        job.status = "succeeded"
    except Exception as e:  # noqa: BLE001
        failed = next((st for st in job.steps if st.status == "running"), None)
        if failed:
            failed.status = "failed"
            failed.detail = _message(e)
        job.error = _error_dict(e)
        job.status = "failed"
    finally:
        job.finished_at = time.time()
    return job


def _message(e: Exception) -> str:
    return e.message if isinstance(e, OpnsenseError) else str(e)


def _error_dict(e: Exception) -> dict:
    if isinstance(e, OpnsenseError):
        return e.to_dict()
    return {"error": type(e).__name__, "message": str(e)}
