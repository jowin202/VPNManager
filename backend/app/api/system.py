from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ..config import MIN_OPNSENSE_VERSION
from ..context import AppContext, VersionTooOld
from ..opnsense.errors import OpnsenseAuthError, OpnsenseError, OpnsenseNotFound
from .deps import get_ctx

# Answer quickly when OPNsense is down instead of waiting for all read retries
REACHABILITY_TIMEOUT = 10.0

router = APIRouter(prefix="/api/system", tags=["System"])

# One read-only probe per required privilege
PRIVILEGE_PROBES = {
    "WireGuard": "wireguard/server/search_server",
    "Interfaces: Assignments": "interfaces/assignment/search_item",
    "System: Gateways": "routing/settings/search_gateway",
    "Gateway-Status": "routes/gateway/status",
    "Firewall: Rules": "firewall/filter/search_rule",
    "Firewall: Source NAT": "firewall/source_nat/search_rule",
    "Firewall: Aliases": "firewall/alias/search_item",
    "Host Discovery": "hostdiscovery/service/search",
    "Diagnostics: ARP": "diagnostics/interface/search_arp",
}


@router.get("/check")
async def check(ctx: AppContext = Depends(get_ctx)) -> dict:
    s = ctx.settings
    result: dict = {
        "opnsense_url": s.opnsense_url,
        "min_version": ".".join(map(str, MIN_OPNSENSE_VERSION)),
        "version": None,
        "version_ok": False,
        "reachable": False,
        "privileges": [],
        "warnings": [w for w in [s.tls_warning] if w],
        "hints": [
            "The firewall setting \"Skip rules when gateway is down\" affects the kill switch: "
            "when enabled, the gateway rule is skipped while the tunnel is down and the block rule applies. "
            "When disabled, the rule is evaluated without the gateway. The VPN Manager does not change it.",
            "Assignments are IP-based: hosts need a fixed IP or a DHCP reservation.",
            "The kill switch covers IPv4 only. Hosts with IPv6 can bypass the VPN via WAN IPv6.",
        ],
        "error": None,
    }
    try:
        ctx.version = None
        result["version"] = await asyncio.wait_for(ctx.ops.system.version(), REACHABILITY_TIMEOUT)
        result["reachable"] = True
        ctx.version = result["version"]
        if s.skip_version_check:
            result["warnings"].append("Version check is disabled (VPNMGR_SKIP_VERSION_CHECK).")
        await ctx.ensure_version()
        result["version_ok"] = True
    except VersionTooOld as e:
        result["error"] = str(e)
    except OpnsenseError as e:
        result["error"] = e.message
    except asyncio.TimeoutError:
        result["error"] = f"OPNsense did not answer within {REACHABILITY_TIMEOUT:.0f} seconds."

    if result["reachable"]:
        for label, path in PRIVILEGE_PROBES.items():
            try:
                await ctx.ops.client.get(path)
                status = "ok"
            except OpnsenseAuthError:
                status = "missing"
            except OpnsenseNotFound:
                status = "unavailable"
            except OpnsenseError:
                status = "error"
            result["privileges"].append({"name": label, "endpoint": path, "status": status})
    result["ok"] = (
        result["version_ok"]
        and all(p["status"] == "ok" for p in result["privileges"] if p["name"] != "Host Discovery")
    )
    return result
